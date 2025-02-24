"""Robinhood.py: a collection of utilities for working with Robinhood's Private API """

#Standard libraries
import logging
import warnings

from enum import Enum

#External dependencies
from six.moves.urllib.parse import unquote  # pylint: disable=E0401
from six.moves.urllib.request import getproxies  # pylint: disable=E0401
from six.moves import input

import getpass
import requests
import six
import dateutil
from dateutil.rrule import DAILY, rrule, MO, TU, WE, TH, FR

from datetime import datetime, timedelta
import os
import csv
import copy

#Application-specific imports
from . import exceptions as RH_exception
from . import endpoints
from RobinhoodOrder import RobinhoodOrder, getOrderFromDict
from IexStock import IexStock, get_stock_cache
import matplotlib.pyplot as plt

RH_CACHE_DIR = ".rh_cache"
def daterange(start_date, end_date):
    return rrule(DAILY, dtstart=start_date, until=end_date, byweekday=(MO,TU,WE,TH,FR))

# https://stackoverflow.com/questions/4039879/best-way-to-find-the-months-between-two-dates/21644877
def diff_month(d1, d2):
    return (d1.year - d2.year) * 12 + d1.month - d2.month

def sum_dict(d):
    res = 0
    for k in d:
        res += d[k]
    return res

class Bounds(Enum):
    """Enum for bounds in `historicals` endpoint """

    REGULAR = 'regular'
    EXTENDED = 'extended'


class Transaction(Enum):
    """Enum for buy/sell orders """

    BUY = 'buy'
    SELL = 'sell'


class Robinhood:
    """Wrapper class for fetching/parsing Robinhood endpoints """

    session = None
    username = None
    password = None
    headers = None
    auth_token = None
    refresh_token = None

    logger = logging.getLogger('Robinhood')
    logger.addHandler(logging.NullHandler())

    client_id = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"


    ###########################################################################
    #                       Logging in and initializing
    ###########################################################################

    def __init__(self):
        self.session = requests.session()
        self.session.proxies = getproxies()
        self.headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en;q=1, fr;q=0.9, de;q=0.8, ja;q=0.7, nl;q=0.6, it;q=0.5",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "X-Robinhood-API-Version": "1.0.0",
            "Connection": "keep-alive",
            "User-Agent": "Robinhood/823 (iPhone; iOS 7.1.2; Scale/2.00)"
        }
        self.session.headers = self.headers
        self.auth_method = self.login_prompt
        self.instrument_cache = {}
        if not os.path.exists(RH_CACHE_DIR):
            os.mkdir(RH_CACHE_DIR)

    def login_required(function):  # pylint: disable=E0213
        """ Decorator function that prompts user for login if they are not logged in already. Can be applied to any function using the @ notation. """
        def wrapper(self, *args, **kwargs):
            if 'Authorization' not in self.headers:
                self.auth_method()
            return function(self, *args, **kwargs)  # pylint: disable=E1102
        return wrapper

    def login_prompt(self):  # pragma: no cover
        """Prompts user for username and password and calls login() """

        username = input("Username: ")
        password = getpass.getpass()

        return self.login(username=username, password=password)


    def login(self,
              username,
              password,
              device_token,
              mfa_code=None):
        """Save and test login info for Robinhood accounts

        Args:
            username (str): username
            password (str): password

        Returns:
            (bool): received valid auth token

        """

        self.username = username
        payload = {
            'password': password,
            'username': self.username,
            'grant_type': 'password',
            'client_id': self.client_id,
            'device_token': device_token
        }

        if mfa_code:
            payload['mfa_code'] = mfa_code
        try:
            res = self.session.post(endpoints.login(), data=payload, timeout=15)
            res.raise_for_status()
            data = res.json()
        except requests.exceptions.HTTPError:
            raise RH_exception.LoginFailed()

        if 'mfa_required' in data.keys():           # pragma: no cover
            mfa_code = input("MFA: ")
            return self.login(username,password,mfa_code)

        if 'access_token' in data.keys() and 'refresh_token' in data.keys():
            self.auth_token = data['access_token']
            self.refresh_token = data['refresh_token']
            self.headers['Authorization'] = 'Bearer ' + self.auth_token
            return True

        return False


    def logout(self):
        """Logout from Robinhood

        Returns:
            (:obj:`requests.request`) result from logout endpoint

        """

        try:
            payload = {
                'client_id': self.client_id,
                'token': self.refresh_token
            }
            req = self.session.post(endpoints.logout(), data=payload, timeout=15)
            req.raise_for_status()
        except requests.exceptions.HTTPError as err_msg:
            warnings.warn('Failed to log out ' + repr(err_msg))

        self.headers['Authorization'] = None
        self.auth_token = None

        return req


    ###########################################################################
    #                               GET DATA
    ###########################################################################

    def investment_profile(self):
        """Fetch investment_profile """

        res = self.session.get(endpoints.investment_profile(), timeout=15)
        res.raise_for_status()  # will throw without auth
        data = res.json()

        return data


    def instruments(self, stock):
        """Fetch instruments endpoint

            Args:
                stock (str): stock ticker

            Returns:
                (:obj:`dict`): JSON contents from `instruments` endpoint
        """

        res = self.session.get(endpoints.instruments(), params={'query': stock.upper()}, timeout=15)
        res.raise_for_status()
        res = res.json()

        # if requesting all, return entire object so may paginate with ['next']
        if (stock == ""):
            return res

        return res['results']


    def instrument(self, id):
        """Fetch instrument info

            Args:
                id (str): instrument id

            Returns:
                (:obj:`dict`): JSON dict of instrument
        """
        url = str(endpoints.instruments()) + "?symbol=" + str(id)

        try:
            req = requests.get(url, timeout=15)
            req.raise_for_status()
            data = req.json()
        except requests.exceptions.HTTPError:
            raise RH_exception.InvalidInstrumentId()

        return data['results']


    def quote_data(self, stock=''):
        """Fetch stock quote

            Args:
                stock (str): stock ticker, prompt if blank

            Returns:
                (:obj:`dict`): JSON contents from `quotes` endpoint
        """

        url = None

        if stock.find(',') == -1:
            url = str(endpoints.quotes()) + str(stock) + "/"
        else:
            url = str(endpoints.quotes()) + "?symbols=" + str(stock)

        #Check for validity of symbol
        try:
            req = self.session.get(url, timeout=15)
            req.raise_for_status()
            data = req.json()
        except requests.exceptions.HTTPError:
            raise RH_exception.InvalidTickerSymbol()


        return data


    # We will keep for compatibility until next major release
    def quotes_data(self, stocks):
        """Fetch quote for multiple stocks, in one single Robinhood API call

            Args:
                stocks (list<str>): stock tickers

            Returns:
                (:obj:`list` of :obj:`dict`): List of JSON contents from `quotes` endpoint, in the
                    same order of input args. If any ticker is invalid, a None will occur at that position.
        """

        url = str(endpoints.quotes()) + "?symbols=" + ",".join(stocks)

        try:
            req = self.session.get(url, timeout=15)
            req.raise_for_status()
            data = req.json()
        except requests.exceptions.HTTPError:
            raise RH_exception.InvalidTickerSymbol()


        return data["results"]


    def get_quote_list(self,
                       stock='',
                       key=''):
        """Returns multiple stock info and keys from quote_data (prompt if blank)

            Args:
                stock (str): stock ticker (or tickers separated by a comma)
                , prompt if blank
                key (str): key attributes that the function should return

            Returns:
                (:obj:`list`): Returns values from each stock or empty list
                               if none of the stocks were valid

        """

        #Creates a tuple containing the information we want to retrieve
        def append_stock(stock):
            keys = key.split(',')
            myStr = ''
            for item in keys:
                myStr += stock[item] + ","

            return (myStr.split(','))


        #Prompt for stock if not entered
        if not stock:   # pragma: no cover
            stock = input("Symbol: ")

        data = self.quote_data(stock)
        res = []

        # Handles the case of multple tickers
        if stock.find(',') != -1:
            for stock in data['results']:
                if stock is None:
                    continue
                res.append(append_stock(stock))

        else:
            res.append(append_stock(data))

        return res


    def get_quote(self, stock=''):
        """Wrapper for quote_data """

        data = self.quote_data(stock)
        return data

    def get_historical_quotes(self, stock, interval, span, bounds=Bounds.REGULAR):
        """Fetch historical data for stock

            Note: valid interval/span configs
                interval = 5minute | 10minute + span = day, week
                interval = day + span = year
                interval = week
                TODO: NEEDS TESTS

            Args:
                stock (str): stock ticker
                interval (str): resolution of data
                span (str): length of data
                bounds (:enum:`Bounds`, optional): 'extended' or 'regular' trading hours

            Returns:
                (:obj:`dict`) values returned from `historicals` endpoint
        """
        if type(stock) is str:
            stock = [stock]

        if isinstance(bounds, str):  # recast to Enum
            bounds = Bounds(bounds)

        historicals = endpoints.historicals() + "/?symbols=" + ','.join(stock).upper() + "&interval=" + interval + "&span=" + span + "&bounds=" + bounds.name.lower()

        res = self.session.get(historicals, timeout=15)
        return res.json()['results'][0]


    def get_news(self, stock):
        """Fetch news endpoint
            Args:
                stock (str): stock ticker

            Returns:
                (:obj:`dict`) values returned from `news` endpoint
        """

        return self.session.get(endpoints.news(stock.upper()), timeout=15).json()


    def print_quote(self, stock=''):    # pragma: no cover
        """Print quote information
            Args:
                stock (str): ticker to fetch

            Returns:
                None
        """

        data = self.get_quote_list(stock, 'symbol,last_trade_price')
        for item in data:
            quote_str = item[0] + ": $" + item[1]
            self.logger.info(quote_str)


    def print_quotes(self, stocks):  # pragma: no cover
        """Print a collection of stocks

            Args:
                stocks (:obj:`list`): list of stocks to pirnt

            Returns:
                None
        """

        if stocks is None:
            return

        for stock in stocks:
            self.print_quote(stock)


    def ask_price(self, stock=''):
        """Get asking price for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (float): ask price
        """

        return self.get_quote_list(stock, 'ask_price')


    def ask_size(self, stock=''):
        """Get ask size for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (int): ask size
        """

        return self.get_quote_list(stock, 'ask_size')


    def bid_price(self, stock=''):
        """Get bid price for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (float): bid price
        """

        return self.get_quote_list(stock, 'bid_price')


    def bid_size(self, stock=''):
        """Get bid size for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (int): bid size
        """

        return self.get_quote_list(stock, 'bid_size')


    def last_trade_price(self, stock=''):
        """Get last trade price for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (float): last trade price
        """

        return self.get_quote_list(stock, 'last_trade_price')


    def previous_close(self, stock=''):
        """Get previous closing price for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (float): previous closing price
        """

        return self.get_quote_list(stock, 'previous_close')


    def previous_close_date(self, stock=''):
        """Get previous closing date for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (str): previous close date
        """

        return self.get_quote_list(stock, 'previous_close_date')


    def adjusted_previous_close(self, stock=''):
        """Get adjusted previous closing price for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (float): adjusted previous closing price
        """

        return self.get_quote_list(stock, 'adjusted_previous_close')


    def symbol(self, stock=''):
        """Get symbol for a stock

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (str): stock symbol
        """

        return self.get_quote_list(stock, 'symbol')


    def last_updated_at(self, stock=''):
        """Get last update datetime

            Note:
                queries `quote` endpoint, dict wrapper

            Args:
                stock (str): stock ticker

            Returns:
                (str): last update datetime
        """

        return self.get_quote_list(stock, 'last_updated_at')


    def last_updated_at_datetime(self, stock=''):
        """Get last updated datetime

            Note:
                queries `quote` endpoint, dict wrapper
                `self.last_updated_at` returns time as `str` in format: 'YYYY-MM-ddTHH:mm:ss:000Z'

            Args:
                stock (str): stock ticker

            Returns:
                (datetime): last update datetime

        """

        #Will be in format: 'YYYY-MM-ddTHH:mm:ss:000Z'
        datetime_string = self.last_updated_at(stock)
        result = dateutil.parser.parse(datetime_string)

        return result

    def get_account(self):
        """Fetch account information

            Returns:
                (:obj:`dict`): `accounts` endpoint payload
        """

        res = self.session.get(endpoints.accounts(), timeout=15)
        res.raise_for_status()  # auth required
        res = res.json()

        return res['results'][0]


    def get_url(self, url):
        """
            Flat wrapper for fetching URL directly
        """

        return self.session.get(url, timeout=15).json()

    def get_popularity(self, stock=''):
        """Get the number of robinhood users who own the given stock

            Args:
                stock (str): stock ticker

            Returns:
                (int): number of users who own the stock
        """
        stock_instrument = self.get_url(self.quote_data(stock)["instrument"])["id"]
        return self.get_url(endpoints.instruments(stock_instrument, "popularity"))["num_open_positions"]

    def get_tickers_by_tag(self, tag=None):
        """Get a list of instruments belonging to a tag

            Args: tag - Tags may include but are not limited to:
                * top-movers
                * etf
                * 100-most-popular
                * mutual-fund
                * finance
                * cap-weighted
                * investment-trust-or-fund

            Returns:
                (List): a list of Ticker strings
        """
        instrument_list = self.get_url(endpoints.tags(tag))["instruments"]
        return [self.get_url(instrument)["symbol"] for instrument in instrument_list]

    @login_required
    def get_transfers(self):
        """Returns a page of list of transfers made to/from the Bank.

        Note that this is a paginated response. The consumer will have to look
        at 'next' key in the JSON and make a subsequent request for the next
        page.

            Returns:
                (list): List of all transfers to/from the bank.
        """
        res = self.session.get(endpoints.ach('transfers'), timeout=15)
        res.raise_for_status()
        return res.json()

    @login_required
    def get_all_transfers(self):
        res = {
            'next': endpoints.ach('transfers'),
        }
        result = []
        while res['next'] is not None:
            next_url = res['next']
            res = self.session.get(next_url, timeout=15).json()
            for log in res['results']:
                # TODO oop-ify this
                result.append({
                    # 'date': log['expected_landing_date'].split("T")[0],
                    'date': log['created_at'].split("T")[0],
                    'amount': log['amount'],
                    'type': log['direction']
                })
        return result

    ###########################################################################
    #                           GET OPTIONS INFO
    ###########################################################################

    def get_options(self, stock, expiration_dates, option_type):
        """Get a list (chain) of options contracts belonging to a particular stock

            Args: stock ticker (str), list of expiration dates to filter on (YYYY-MM-DD), and whether or not its a 'put' or a 'call' option type (str).

            Returns:
                Options Contracts (List): a list (chain) of contracts for a given underlying equity instrument
        """
        instrument_id = self.get_url(self.quote_data(stock)["instrument"])["id"]
        if (type(expiration_dates) == list):
            _expiration_dates_string = ",".join(expiration_dates)
        else:
            _expiration_dates_string = expiration_dates
        chain_id = self.get_url(endpoints.chain(instrument_id))["results"][0]["id"]
        return [contract for contract in self.get_url(endpoints.options(chain_id, _expiration_dates_string, option_type))["results"]]

    @login_required
    def get_option_market_data(self, optionid):
        """Gets a list of market data for a given optionid.

        Args: (str) option id

        Returns: dictionary of options market data.
        """
        market_data = {}
        try:
            market_data = self.get_url(endpoints.market_data(optionid)) or {}
        except requests.exceptions.HTTPError:
            raise RH_exception.InvalidOptionId()
        return market_data


    ###########################################################################
    #                           GET FUNDAMENTALS
    ###########################################################################

    def get_fundamentals(self, stock=''):
        """Find stock fundamentals data

            Args:
                (str): stock ticker

            Returns:
                (:obj:`dict`): contents of `fundamentals` endpoint
        """

        #Prompt for stock if not entered
        if not stock:   # pragma: no cover
            stock = input("Symbol: ")

        url = str(endpoints.fundamentals(str(stock.upper())))

        #Check for validity of symbol
        try:
            req = self.session.get(url, timeout=15)
            req.raise_for_status()
            data = req.json()
        except requests.exceptions.HTTPError:
            raise RH_exception.InvalidTickerSymbol()


        return data


    def fundamentals(self, stock=''):
        """Wrapper for get_fundamentlals function """

        return self.get_fundamentals(stock)


    ###########################################################################
    #                           PORTFOLIOS DATA
    ###########################################################################

    def portfolios(self):
        """Returns the user's portfolio data """

        req = self.session.get(endpoints.portfolios(), timeout=15)
        req.raise_for_status()

        return req.json()['results'][0]


    def adjusted_equity_previous_close(self):
        """Wrapper for portfolios

            Returns:
                (float): `adjusted_equity_previous_close` value

        """

        return float(self.portfolios()['adjusted_equity_previous_close'])


    def equity(self):
        """Wrapper for portfolios

            Returns:
                (float): `equity` value
        """

        return float(self.portfolios()['equity'])


    def equity_previous_close(self):
        """Wrapper for portfolios

            Returns:
                (float): `equity_previous_close` value
        """

        return float(self.portfolios()['equity_previous_close'])


    def excess_margin(self):
        """Wrapper for portfolios

            Returns:
                (float): `excess_margin` value
        """

        return float(self.portfolios()['excess_margin'])


    def extended_hours_equity(self):
        """Wrapper for portfolios

            Returns:
                (float): `extended_hours_equity` value
        """

        try:
            return float(self.portfolios()['extended_hours_equity'])
        except TypeError:
            return None


    def extended_hours_market_value(self):
        """Wrapper for portfolios

            Returns:
                (float): `extended_hours_market_value` value
        """

        try:
            return float(self.portfolios()['extended_hours_market_value'])
        except TypeError:
            return None


    def last_core_equity(self):
        """Wrapper for portfolios

            Returns:
                (float): `last_core_equity` value
        """

        return float(self.portfolios()['last_core_equity'])


    def last_core_market_value(self):
        """Wrapper for portfolios

            Returns:
                (float): `last_core_market_value` value
        """

        return float(self.portfolios()['last_core_market_value'])


    def market_value(self):
        """Wrapper for portfolios

            Returns:
                (float): `market_value` value
        """

        return float(self.portfolios()['market_value'])

    @login_required
    def order_history(self, orderId=None):
        """Wrapper for portfolios
            Optional Args: add an order ID to retrieve information about a single order.
            Returns:
                (:obj:`dict`): JSON dict from getting orders
        """

        return self.session.get(endpoints.orders(orderId), timeout=15).json()

    def get_cached_order_history(self):
        files = sorted(os.listdir("{}/order_history".format(RH_CACHE_DIR)))
        history = []
        with open("{}/order_history/{}".format(RH_CACHE_DIR, files[-1]), "r") as f:
            print("[INFO] Found cached order history file: ", files[-1])
            csv_reader = csv.reader(f, delimiter=",")
            headers = None
            for row in csv_reader:
                if headers is None:
                    headers = [k for k in row]
                else:
                    d = {}
                    for i in range(len(row)):
                        d[headers[i]] = row[i]
                    history.append(getOrderFromDict(d))
        # TODO: Add schema validation to make sure this is coherent with normal fn
        # Basically check that history objects conform to a RobinhoodOrder object

        # TODO Just always use cached file, and just add onto it if we detect anything new
        return history

    @login_required
    def full_order_history(self, use_cache=True, start=None, end=None):
        """
            Returns array of order objects
        """
        # if use_cache, check for local file
        if (use_cache and 
        os.path.exists("{}/order_history".format(RH_CACHE_DIR)) and 
        len(os.listdir("{}/order_history".format(RH_CACHE_DIR))) > 0):
            return self.get_cached_order_history()

        print("[INFO] Not using cache, retrieving order history data from Robinhood")
        # return it if it's not too long ago? 
        res = self.order_history()
        result = []
        while res['next'] is not None:
            next_url = res['next']
            result += res['results']
            res = self.session.get(next_url, timeout=15).json()
        if res['results'] is not None:
            result += res['results']
        history = []
        for log in result[::-1]:
            symbol = self.instrument_lookup(log['instrument'])
            if log['state'] == 'filled':
                history.append(RobinhoodOrder(
                    symbol, 
                    log['side'], 
                    log['quantity'], 
                    log['executions'][-1]['price'], 
                    log['executions'][-1]['timestamp']))

        # Write to local cache
        if len(history) > 0:
            if not os.path.exists("{}/order_history".format(RH_CACHE_DIR)):
                os.mkdir("{}/order_history".format(RH_CACHE_DIR))
            with open("{}/order_history/{}.csv".format(RH_CACHE_DIR, str(datetime.now())), "w") as f:
                csv_writer = csv.writer(f, delimiter=",", quotechar='"')
                csv_writer.writerow(history[0].getCsvHeader())
                for log in history:
                    csv_writer.writerow(log.getCsvRow())
        return history

    def portfolio_history(self, order_history=None, use_cache=True):
        if order_history is None: order_history = self.full_order_history()
        """
            Returns dict which maps date to portfolio at that timestep
            This has some tricky pointer shortcuts so be careful of bugs here
        """
        # Check to make sure order history is sorted chronologically
        order_history = sorted(order_history, key=lambda x: x.date)

        running_portfolio = {}
        port_hist = {}
        all_stocks = set()

        for log in order_history:
            all_stocks.add(log.symbol)
            date = log.getDate()
            if date not in port_hist:
                running_portfolio = copy.deepcopy(running_portfolio)
                port_hist[date] = running_portfolio
            if log.symbol not in running_portfolio:
                running_portfolio[log.symbol] = 0
            if log.action == "sell":
                running_portfolio[log.symbol] -= float(log.shares)
            else:
                running_portfolio[log.symbol] += float(log.shares)

        start_date = datetime.strptime(sorted(port_hist.keys())[0], "%Y-%m-%d")
        end_date = datetime.now()

        # Write to local cache and format result
        # Format will be like 
        # date,stock1,stock2,stock3
        # xx.xx.xx,0,0,0,0
        if not os.path.exists("{}/port_history".format(RH_CACHE_DIR)):
            os.mkdir("{}/port_history".format(RH_CACHE_DIR))

        result = {}
        with open("{}/port_history/{}.csv".format(RH_CACHE_DIR, str(datetime.now())), "w") as f:
            csv_writer = csv.writer(f, delimiter=",", quotechar='"')
            running_portfolio = None
            header = ["date"]
            for stock in all_stocks:
                header.append(stock)
            csv_writer.writerow(header)
            for date in daterange(start_date, end_date):
                strdate = datetime.strftime(date, "%Y-%m-%d")
                if strdate in port_hist: 
                    running_portfolio = port_hist[strdate]
                row = [strdate]
                for stock in all_stocks:
                    stock_shares = 0 if stock not in running_portfolio else running_portfolio[stock] 
                    row.append(stock_shares)
                csv_writer.writerow(row)
                result[strdate] = {}
                for i in range(len(header)):
                    if header[i] != "date":
                        result[strdate][header[i]] = row[i]
        return result

    def get_stock_costs(self, order_history, port_hist):
        cost_cache = {}
        for date in port_hist:
            cost_cache[date] = { stock: 0 for stock in port_hist[date] }
        for log in order_history:
            date = log.getDate()
            while date not in cost_cache:
                date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            cost_cache[date][log.symbol] += log.getPrice()
        sum_cost_cache = {}
        running_cost_cache = { stock: 0 for stock in cost_cache[sorted(cost_cache.keys())[0]] }
        for date in sorted(cost_cache.keys()):
            for stock in cost_cache[date]:
                running_cost_cache[stock] += cost_cache[date][stock]
            sum_cost_cache[date] = copy.deepcopy(running_cost_cache)
        return sum_cost_cache

    def save_stock_prices(self, port_hist, api_key=""):
        start_date = sorted(port_hist.keys())[0]
        end_date = sorted(port_hist.keys())[-1]
        stocks = port_hist[start_date].keys()

        iex = IexStock(api_key)

        if not os.path.exists("{}/historical_prices".format(RH_CACHE_DIR)):
            os.mkdir("{}/historical_prices".format(RH_CACHE_DIR))

        for stock in stocks: 
            filepath = "{}/historical_prices/{}.csv".format(RH_CACHE_DIR, stock)
            if not os.path.exists(filepath):
                print("[INFO] Unable to find cached stock price for {}, retrieving past 15 years of stock data".format(stock))
                data = iex.hist_data(stock, "max")
                with open(filepath, "w") as f:
                    csv_writer = csv.writer(f, delimiter=",")
                    csv_writer.writerow(["date, close"])
                    for log in data:
                        csv_writer.writerow([log['date'], log['close']])
            else:
                with open(filepath, "r") as f:
                    csv_reader = csv.reader(f, delimiter=",")
                    for row in csv_reader:
                        last_date = row[0]
                if last_date != end_date:
                    ld = datetime.strptime(last_date, "%Y-%m-%d")
                    ed = datetime.strptime(end_date, "%Y-%m-%d")
                    if (ed.year - ld.year) >= 2: # Difference is AT LEAST 2 years
                        request_range = "max"
                    elif (ed.year - ld.year) == 1: # Difference is AT LEAST a year
                        request_range = "2y"
                    elif diff_month(ed, ld) > 6: # Difference is AT 6 months
                        request_range = "1y"
                    elif diff_month(ed, ld) > 3:
                        request_range = "6m"
                    elif diff_month(ed, ld) > 1:
                        request_range = "3m"
                    elif (ed - ld).days > 5:
                        request_range = "1m"
                    else:
                        request_range = "5d"
                    print("[INFO] Found {} needs updating. Last saved date: {} Portfolio end date: {} Range: {}".format(stock, last_date, end_date, request_range))
                    data = iex.hist_data(stock, request_range)
                    with open(filepath, "a") as f:
                        csv_writer = csv.writer(f, delimiter=",")
                        for log in data:
                            logdate = datetime.strptime(log['date'], "%Y-%m-%d")
                            if logdate > ld:
                                csv_writer.writerow([log['date'], log['close']])

    def instrument_lookup(self, instrument_url):
        # TODO maybe?? add a caching layer for this if we actually care
        if instrument_url in self.instrument_cache:
            return self.instrument_cache[instrument_url]
        res = self.session.get(instrument_url, timeout=15).json()
        self.instrument_cache[instrument_url] = res['symbol']
        return res['symbol']

    def time_weighted_returns(self, port_hist, transfer_hist, stock_costs):
        start_date = sorted(port_hist.keys())[0]
        end_date = sorted(port_hist.keys())[-1] 
        stock_cache = get_stock_cache("{}/historical_prices".format(RH_CACHE_DIR), start_date, end_date)
        stocks = port_hist[start_date].keys()

        # TODO make sure stock cache is valid

        cash_flow = {}
        port_value = {}

        for day in sorted(port_hist.keys()):
            portfolio = port_hist[day]
            port_stock_value = 0
            for stock in portfolio:
                stock_value = 0 if day not in stock_cache or stock not in stock_cache[day] else float(stock_cache[day][stock])
                shares = float(portfolio[stock])
                port_stock_value += stock_value * shares
            if port_stock_value != 0:
                port_value[day] = port_stock_value
                cash_flow[day] = 0 # If port stock value is 0, this is a holiday

        for log in transfer_hist:
            date = log['date']
            while date not in cash_flow:
                date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if log['type'] == 'deposit':
                cash_flow[date] += float(log['amount'])
            else:
                cash_flow[date] -= float(log['amount'])
        
        cum_cash = {}
        sum_cash = 0
        for day in sorted(cash_flow.keys()):
            sum_cash += cash_flow[day]
            cum_cash[day] = sum_cash

        for day in port_value:
            port_value[day] += cum_cash[day] + sum_dict(stock_costs[day])

        prev_date = None
        cum_product = 1
        twr = {}
        for day in sorted(port_value.keys()):
            if prev_date is None:
                prev_date = day
            else:
                cur_cash_flow = cash_flow[day] 
                end_val = port_value[day]
                init_val = port_value[prev_date]
                hpr_n = (end_val - (init_val + cur_cash_flow)) / (init_val + cur_cash_flow)
                cum_product *= (hpr_n + 1)
                prev_date = day
                twr[day] = (cum_product - 1) * 100

        xaxis = [d for d in sorted(twr.keys())]
        yaxis = [twr[d] for d in xaxis]

        for d in xaxis:
            print(d, twr[d])

        num_xaxis = [i for i in range(len(xaxis))]
        plt.plot(num_xaxis, yaxis)
        plt.show()
        return twr

    def dividends(self):
        """Wrapper for portfolios

            Returns:
                (:obj: `dict`): JSON dict from getting dividends
        """

        return self.session.get(endpoints.dividends(), timeout=15).json()


    ###########################################################################
    #                           POSITIONS DATA
    ###########################################################################

    def positions(self):
        """Returns the user's positions data

            Returns:
                (:object: `dict`): JSON dict from getting positions
        """

        return self.session.get(endpoints.positions(), timeout=15).json()


    def securities_owned(self):
        """Returns list of securities' symbols that the user has shares in

            Returns:
                (:object: `dict`): Non-zero positions
        """

        return self.session.get(endpoints.positions() + '?nonzero=true', timeout=15).json()


    ###########################################################################
    #                               PLACE ORDER
    ###########################################################################

    def place_order(self,
                    instrument,
                    quantity=1,
                    price=0.0,
                    transaction=None,
                    trigger='immediate',
                    order='market',
                    time_in_force='gfd'):
        """Place an order with Robinhood

            Notes:
                OMFG TEST THIS PLEASE!

                Just realized this won't work since if type is LIMIT you need to use "price" and if
                a STOP you need to use "stop_price".  Oops.
                Reference: https://github.com/sanko/Robinhood/blob/master/Order.md#place-an-order

            Args:
                instrument (dict): the RH URL and symbol in dict for the instrument to be traded
                quantity (int): quantity of stocks in order
                bid_price (float): price for order
                transaction (:enum:`Transaction`): BUY or SELL enum
                trigger (:enum:`Trigger`): IMMEDIATE or STOP enum
                order (:enum:`Order`): MARKET or LIMIT
                time_in_force (:enum:`TIME_IN_FORCE`): GFD or GTC (day or until cancelled)

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """

        if isinstance(transaction, str):
            transaction = Transaction(transaction)

        if not price:
            price = self.quote_data(instrument['symbol'])['bid_price']

        payload = {
            'account': self.get_account()['url'],
            'instrument': unquote(instrument['url']),
            'quantity': quantity,
            'side': transaction.name.lower(),
            'symbol': instrument['symbol'],
            'time_in_force': time_in_force.lower(),
            'trigger': trigger,
            'type': order.lower()
        }

        if order.lower() == "stop":
            payload['stop_price'] = float(price)
        else:
            payload['price'] = float(price)

        res = self.session.post(endpoints.orders(), data=payload, timeout=15)
        res.raise_for_status()

        return res


    def place_buy_order(self,
                        instrument,
                        quantity,
                        bid_price=0.0):
        """Wrapper for placing buy orders

            Args:
                instrument (dict): the RH URL and symbol in dict for the instrument to be traded
                quantity (int): quantity of stocks in order
                bid_price (float): price for order

            Returns:
                (:obj:`requests.request`): result from `orders` put command

        """

        transaction = Transaction.BUY

        return self.place_order(instrument, quantity, bid_price, transaction)


    def place_sell_order(self,
                         instrument,
                         quantity,
                         bid_price=0.0):
        """Wrapper for placing sell orders

            Args:
                instrument (dict): the RH URL and symbol in dict for the instrument to be traded
                quantity (int): quantity of stocks in order
                bid_price (float): price for order

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """

        transaction = Transaction.SELL

        return self.place_order(instrument, quantity, bid_price, transaction)

    # Methods below here are a complete rewrite for buying and selling
    # These are new. Use at your own risk!

    def place_market_buy_order(self,
                               instrument_URL=None,
                               symbol=None,
                               time_in_force=None,
                               quantity=None):
        """Wrapper for placing market buy orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                quantity (int): Number of shares to buy

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='market',
                                 trigger='immediate',
                                 side='buy',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 quantity=quantity))

    def place_limit_buy_order(self,
                              instrument_URL=None,
                              symbol=None,
                              time_in_force=None,
                              price=None,
                              quantity=None):
        """Wrapper for placing limit buy orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                price (float): The max price you're willing to pay per share
                quantity (int): Number of shares to buy

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='limit',
                                 trigger='immediate',
                                 side='buy',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 price=price,
                                 quantity=quantity))

    def place_stop_loss_buy_order(self,
                                  instrument_URL=None,
                                  symbol=None,
                                  time_in_force=None,
                                  stop_price=None,
                                  quantity=None):
        """Wrapper for placing stop loss buy orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                stop_price (float): The price at which this becomes a market order
                quantity (int): Number of shares to buy

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='market',
                                 trigger='stop',
                                 side='buy',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 stop_price=stop_price,
                                 quantity=quantity))

    def place_stop_limit_buy_order(self,
                                   instrument_URL=None,
                                   symbol=None,
                                   time_in_force=None,
                                   stop_price=None,
                                   price=None,
                                   quantity=None):
        """Wrapper for placing stop limit buy orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                stop_price (float): The price at which this becomes a limit order
                price (float): The max price you're willing to pay per share
                quantity (int): Number of shares to buy

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='limit',
                                 trigger='stop',
                                 side='buy',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 stop_price=stop_price,
                                 price=price,
                                 quantity=quantity))

    def place_market_sell_order(self,
                                instrument_URL=None,
                                symbol=None,
                                time_in_force=None,
                                quantity=None):
        """Wrapper for placing market sell orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                quantity (int): Number of shares to sell

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='market',
                                 trigger='immediate',
                                 side='sell',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 quantity=quantity))

    def place_limit_sell_order(self,
                               instrument_URL=None,
                               symbol=None,
                               time_in_force=None,
                               price=None,
                               quantity=None):
        """Wrapper for placing limit sell orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                price (float): The minimum price you're willing to get per share
                quantity (int): Number of shares to sell

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='limit',
                                 trigger='immediate',
                                 side='sell',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 price=price,
                                 quantity=quantity))

    def place_stop_loss_sell_order(self,
                                   instrument_URL=None,
                                   symbol=None,
                                   time_in_force=None,
                                   stop_price=None,
                                   quantity=None):
        """Wrapper for placing stop loss sell orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                stop_price (float): The price at which this becomes a market order
                quantity (int): Number of shares to sell

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='market',
                                 trigger='stop',
                                 side='sell',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 stop_price=stop_price,
                                 quantity=quantity))

    def place_stop_limit_sell_order(self,
                                    instrument_URL=None,
                                    symbol=None,
                                    time_in_force=None,
                                    price=None,
                                    stop_price=None,
                                    quantity=None):
        """Wrapper for placing stop limit sell orders

            Notes:
                If only one of the instrument_URL or symbol are passed as
                arguments the other will be looked up automatically.

            Args:
                instrument_URL (str): The RH URL of the instrument
                symbol (str): The ticker symbol of the instrument
                time_in_force (str): 'GFD' or 'GTC' (day or until cancelled)
                stop_price (float): The price at which this becomes a limit order
                price (float): The max price you're willing to get per share
                quantity (int): Number of shares to sell

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """
        return(self.submit_order(order_type='limit',
                                 trigger='stop',
                                 side='sell',
                                 instrument_URL=instrument_URL,
                                 symbol=symbol,
                                 time_in_force=time_in_force,
                                 stop_price=stop_price,
                                 price=price,
                                 quantity=quantity))

    def submit_order(self,
                     instrument_URL=None,
                     symbol=None,
                     order_type=None,
                     time_in_force=None,
                     trigger=None,
                     price=None,
                     stop_price=None,
                     quantity=None,
                     side=None):
        """Submits order to Robinhood

            Notes:
                This is normally not called directly.  Most programs should use
                one of the following instead:

                    place_market_buy_order()
                    place_limit_buy_order()
                    place_stop_loss_buy_order()
                    place_stop_limit_buy_order()
                    place_market_sell_order()
                    place_limit_sell_order()
                    place_stop_loss_sell_order()
                    place_stop_limit_sell_order()

            Args:
                instrument_URL (str): the RH URL for the instrument
                symbol (str): the ticker symbol for the instrument
                order_type (str): 'MARKET' or 'LIMIT'
                time_in_force (:enum:`TIME_IN_FORCE`): GFD or GTC (day or
                                                       until cancelled)
                trigger (str): IMMEDIATE or STOP enum
                price (float): The share price you'll accept
                stop_price (float): The price at which the order becomes a
                                    market or limit order
                quantity (int): The number of shares to buy/sell
                side (str): BUY or sell

            Returns:
                (:obj:`requests.request`): result from `orders` put command
        """

        # Used for default price input
        # Price is required, so we use the current bid price if it is not specified
        current_quote = self.get_quote(symbol)
        current_bid_price = current_quote['bid_price']

        # Start with some parameter checks. I'm paranoid about $.
        if(instrument_URL is None):
            if(symbol is None):
                raise(ValueError('Neither instrument_URL nor symbol were passed to submit_order'))
            for result in self.instruments(symbol):
                if result['symbol'].upper() == symbol.upper() :
                    instrument_URL = result['url']
                    break
            if(instrument_URL is None):
                raise(ValueError('instrument_URL could not be defined. Symbol %s not found' % symbol))

        if(symbol is None):
            symbol = self.session.get(instrument_URL, timeout=15).json()['symbol']

        if(side is None):
            raise(ValueError('Order is neither buy nor sell in call to submit_order'))

        if(order_type is None):
            if(price is None):
                if(stop_price is None):
                    order_type = 'market'
                else:
                    order_type = 'limit'

        symbol = str(symbol).upper()
        order_type = str(order_type).lower()
        time_in_force = str(time_in_force).lower()
        trigger = str(trigger).lower()
        side = str(side).lower()

        if(order_type != 'market') and (order_type != 'limit'):
            raise(ValueError('Invalid order_type in call to submit_order'))

        if(order_type == 'limit'):
            if(price is None):
                raise(ValueError('Limit order has no price in call to submit_order'))
            if(price <= 0):
                raise(ValueError('Price must be positive number in call to submit_order'))

        if(trigger == 'stop'):
            if(stop_price is None):
                raise(ValueError('Stop order has no stop_price in call to submit_order'))
            if(stop_price <= 0):
                raise(ValueError('Stop_price must be positive number in call to submit_order'))

        if(stop_price is not None):
            if(trigger != 'stop'):
                raise(ValueError('Stop price set for non-stop order in call to submit_order'))

        if(price is None):
            if(order_type == 'limit'):
                raise(ValueError('Limit order has no price in call to submit_order'))

        if(price is not None):
            if(order_type.lower() == 'market'):
                raise(ValueError('Market order has price limit in call to submit_order'))
            price = float(price)
        else:
            price = current_bid_price # default to current bid price

        if(quantity is None):
            raise(ValueError('No quantity specified in call to submit_order'))

        quantity = int(quantity)

        if(quantity <= 0):
            raise(ValueError('Quantity must be positive number in call to submit_order'))

        payload = {}

        for field, value in [
                ('account', self.get_account()['url']),
                ('instrument', instrument_URL),
                ('symbol', symbol),
                ('type', order_type),
                ('time_in_force', time_in_force),
                ('trigger', trigger),
                ('price', price),
                ('stop_price', stop_price),
                ('quantity', quantity),
                ('side', side)
            ]:
            if(value is not None):
                payload[field] = value

        print(payload)

        res = self.session.post(endpoints.orders(), data=payload, timeout=15)
        res.raise_for_status()

        return res

    ##############################
    #                          CANCEL ORDER
    ##############################

    def cancel_order(
            self,
            order_id):
        """
        Cancels specified order and returns the response (results from `orders` command).
        If order cannot be cancelled, `None` is returned.
        Args:
            order_id (str or dict): Order ID string that is to be cancelled or open order dict returned from
            order get.
        Returns:
            (:obj:`requests.request`): result from `orders` put command
        """
        if isinstance(order_id, str):
            try:
                order = self.session.get(endpoints.orders() + order_id, timeout=15).json()
            except (requests.exceptions.HTTPError) as err_msg:
                raise ValueError('Failed to get Order for ID: ' + order_id
                    + '\n Error message: '+ repr(err_msg))

            if order.get('cancel') is not None:
                try:
                    res = self.session.post(order['cancel'], timeout=15)
                    res.raise_for_status()
                    return res
                except (requests.exceptions.HTTPError) as err_msg:
                    raise ValueError('Failed to cancel order ID: ' + order_id
                         + '\n Error message: '+ repr(err_msg))
                    return None

        if isinstance(order_id, dict):
            order_id = order_id['id']
            try:
                order = self.session.get(endpoints.orders() + order_id, timeout=15).json()
            except (requests.exceptions.HTTPError) as err_msg:
                raise ValueError('Failed to get Order for ID: ' + order_id
                    + '\n Error message: '+ repr(err_msg))

            if order.get('cancel') is not None:
                try:
                    res = self.session.post(order['cancel'], timeout=15)
                    res.raise_for_status()
                    return res
                except (requests.exceptions.HTTPError) as err_msg:
                    raise ValueError('Failed to cancel order ID: ' + order_id
                         + '\n Error message: '+ repr(err_msg))
                    return None

        elif not isinstance(order_id, str) or not isinstance(order_id, dict):
            raise ValueError('Cancelling orders requires a valid order_id string or open order dictionary')


        # Order type cannot be cancelled without a valid cancel link
        else:
            raise ValueError('Unable to cancel order ID: ' + order_id)
