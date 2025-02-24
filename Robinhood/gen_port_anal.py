from . import Robinhood
import getpass
import argparse

parser = argparse.ArgumentParser(description='Analyze robinhood portfolio')
parser.add_argument('--username', '-u', help='Robinhood username', required=True)
parser.add_argument('--password', '-p', help='Robinhood password')
parser.add_argument('--password_file', '-pf', help='Robinhood password file')
parser.add_argument('--device_token', '-d', help='Robinhood device token for login')
parser.add_argument('--api_key', '-a', help='Iex cloud api key')
args = parser.parse_args()

# ============================================
# 0. Log into rh account
# ============================================
device_token = args.device_token
username = args.username
api_key = args.api_key
if args.password:
    password = args.password
elif args.password_file:
    with open(args.password_file, "r+") as f:
        password = f.read().strip()
else:
    password = getpass.getpass()
my_trader = Robinhood()
# TODO see if we even need to log in anymore if we just use cached files
logged_in = my_trader.login(username=username, password=password, device_token=device_token)

# ============================================
# 1. Fetch order history
# ============================================
orders = my_trader.full_order_history()

# ============================================
# 2. Get portfolio history
# ============================================
portfolio_history = my_trader.portfolio_history(order_history=orders)
stock_costs = my_trader.get_stock_costs(orders, portfolio_history)

# ============================================
# 3. Fetch withdrawal / deposit history
# ============================================
transfer_history = my_trader.get_all_transfers()

# ============================================
# 4. Scrape for stock prices
# ============================================
my_trader.save_stock_prices(portfolio_history, api_key=api_key)

# ============================================
# 5. Compute time weighted returns
# ============================================
# TODO: Need to add dividends + options trading
twr = my_trader.time_weighted_returns(portfolio_history, transfer_history, stock_costs)

# ============================================
# 6. Generate ui to view info
# ============================================
