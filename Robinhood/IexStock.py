import requests
import os
import csv

def get_stock_cache(cache_dir, start, end):
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
    result = {}
    for stockcsv in os.listdir(cache_dir):
        filepath = "{}/{}".format(cache_dir, stockcsv)
        with open(filepath, "r") as f:
            csv_reader = csv.reader(f, delimiter=",")
            header = None
            for row in csv_reader:
                if header is None:
                    header = [r for r in row]
                else:
                    date, price = row[0], row[1]
                    stock = stockcsv.replace(".csv", "")
                    if date >= start and date <= end:
                        if date not in result: result[date] = {}
                        result[date][stock] = price
    return result

class IexStock(object):
    def __init__(self, token):
        self.token = token
        self.base_url = "https://cloud.iexapis.com/v1"

    def hist_data(self, symbol, date_range):
        suffix="token={}&chartCloseOnly=true".format(self.token)
        url = "{}/stock/{}/chart/{}?{}".format(self.base_url, symbol, date_range, suffix)
        return requests.get(url).json()
