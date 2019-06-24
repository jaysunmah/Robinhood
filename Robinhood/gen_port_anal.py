from . import Robinhood
import getpass
import argparse

parser = argparse.ArgumentParser(description='Analyze robinhood portfolio')
parser.add_argument('--username', '-u', help='Robinhood username', required=True)
parser.add_argument('--password', '-p', help='Robinhood password')
parser.add_argument('--password_file', '-pf', help='Robinhood password file')
args = parser.parse_args()

# ============================================
# 0. Log into rh account
# ============================================
device_token = 'e86527b8-af87-4ad1-a33f-5589401ddc45'
username = args.username
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

# ============================================
# 3. Scrape for stock prices
# ============================================
my_trader.save_stock_prices(portfolio_history)


# ============================================
# 4. Compute time weighted returns
# ============================================


# ============================================
# 5. Generate ui to view info
# ============================================
