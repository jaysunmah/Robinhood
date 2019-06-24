def getOrderFromDict(d):
    return RobinhoodOrder(d['symbol'],d['action'],d['shares'],d['price'],d['date'])

class RobinhoodOrder(object):
    def __init__(self, symbol, action, shares, price, date):
        self.symbol = symbol
        self.action = action
        self.shares = shares
        self.price = price
        self.date = date
    def __str__(self):
        return "{} {} shares of {} on {} for ${}".format(self.action, self.shares, self.symbol, self.date, self.price)
    def getDate(self):
        return self.date.split("T")[0]
    def getCsvHeader(self):
        return ["symbol", "action", "shares", "price", "date"]
    def getCsvRow(self):
        return [self.symbol, self.action, self.shares, self.price, self.date]