import requests

class IexStock(object):
    def __init__(self, token):
        self.token = token
        self.base_url = "https://cloud.iexapis.com/v1"

    def hist_data(self, symbol, date_range):
        suffix="token={}&chartCloseOnly=true".format(self.token)
        url = "{}/stock/{}/chart/{}?{}".format(self.base_url, symbol, date_range, suffix)
        return requests.get(url).json()

    def get_stock_cache(start, end):
        """
            Returns dict of 
            { 
                start: {
                    stock1: price1,
                    stock2: price2,
                    ...
                },
                date2: {
                    ...
                },
                ...
                end: {
                    ...
                }
            }
            for all stocks found in .rh_cache/historical_prices
        """
        return {}