# -*- coding: utf-8 -*-
# Copyright (c) 2018 Petter Reinholdtsen <pere@hungry.com>
# This file is covered by the GPLv2 or later, read COPYING for details.

import base64
import configparser
import hashlib
import hmac
import simplejson
import time
import unittest
import urllib
import urllib.parse
import tornado.ioloop

from decimal import Decimal
from os.path import expanduser

from valutakrambod.services import Orderbook
from valutakrambod.services import Service
from valutakrambod.services import Trading

class Kraken(Service):
    """
Query the Kraken API.  Documentation is available from
https://www.kraken.com/help/api#general-usage .
"""
    keymap = {
        'BTC' : 'XXBT',
        'XLM' : 'XXLM',
        'EUR' : 'ZEUR',
        'USD' : 'ZUSD',
# Pass these through unchanged
#        'KFEE'
#        'BCH'
        }
    baseurl = "https://api.kraken.com/0/public/"
    privatebaseurl = "https://api.kraken.com/0/private/"
    def servicename(self):
        return "Kraken"

    def ratepairs(self):
        return [
            ('BTC', 'USD'),
            ('BTC', 'EUR'),
            ]
    def _currencyMap(self, currency):
        if currency in self.keymap:
            return self.keymap[currency]
        else:
            return currency
    def _revCurrencyMap(self, asset):
        for stdasset in self.keymap.keys():
            if asset == self.keymap[stdasset]:
                return stdasset
        return asset
    def _makepair(self, f, t):
        return "%s%s" % (self._currencyMap(f), self._currencyMap(t))
    def _nonce(self):
        nonce = self.confgetint('lastnonce', fallback=0) + 1
        # Time based alternative
        #nonce = int(1000*time.time())
        nonce = int(time.time()*1000)
        return nonce
    async def _signedpost(self, url, data):
        urlpath = urllib.parse.urlparse(url).path.encode('UTF-8')
        data['nonce'] = self._nonce()
        datastr = urllib.parse.urlencode(data)

        # API-Sign = Message signature using HMAC-SHA512 of (URI
        # path + SHA256(nonce + POST data)) and base64 decoded
        # secret API key
        noncestr = str(data['nonce'])
        datahash = (noncestr + datastr).encode('UTF-8')
        message = urlpath + hashlib.sha256(datahash).digest()
        msgsignature = hmac.new(base64.b64decode(self.confget('apisecret').encode('UTF-8')),
                                message,
                                hashlib.sha512)
        sign = base64.b64encode(msgsignature.digest()).replace(b'\n', b'')
        headers = {
            'API-Key' : self.confget('apikey'),
            'API-Sign': sign,
            }
        body, response = await self._post(url, datastr, headers)
        return body, response
    async def _query_private(self, method, args):
        url = "%s%s" % (self.privatebaseurl, method)
        body, response = await self._signedpost(url, args)
        j = simplejson.loads(body.decode('UTF-8'), use_decimal=True)
        #print(j)
        if 0 != len(j['error']):
            exceptionmap = {
                'EGeneral:Internal error' : Exception,
                'EAPI:Invalid nonce' : Exception,
                'EOrder:Insufficient funds' : Exception,
            }
            e = Exception
            if j['error'][0] in exceptionmap:
                e = exceptionmap[j['error'][0]]
            raise e('unable to query %s: %s' % (method, j['error']))
        return j['result']
    async def _query_public(self, method, args):
        url = "%s%s" % (self.baseurl, method)
        
        if args:
            url = "%s?%s" % (url, urllib.parse.urlencode(args))
        j, r = await self._jsonget(url)
        return j
    async def fetchRates(self, pairs = None):
        if pairs is None:
            pairs = self.wantedpairs
        #await self._fetchTicker(pairs)
        await self._fetchOrderbooks(pairs)

    async def _fetchOrderbooks(self, pairs):
        now = time.time()
        res = {}
        for pair in pairs:
            pairstr = self._makepair(pair[0], pair[1])
            j = await self._query_public('Depth', {'pair' : pairstr})
            #print(j)
            o = Orderbook()
            for side in ('asks', 'bids'):
                oside = {
                    'asks' : o.SIDE_ASK,
                    'bids' : o.SIDE_BID,
                }[side]
                # For some strange reason, some orders have timestamps
                # in the future.  This is reported to Kraken Support
                # as request 1796106.
                for order in j['result'][pairstr][side]:
                    #print("Updating %s", (side, order), now - order[2])
                    o.update(oside, Decimal(order[0]), Decimal(order[1]), order[2])
                #print(o)
            self.updateOrderbook(pair, o)

    async def _fetchTicker(self, pairs = None):
        if pairs is None:
            pairs = self.ratepairs()
        res = {}
        for p in pairs:
            f = p[0]
            t = p[1]
            pairstr= self._makepair(f, t)
            #print(pairstr)
            j = await self._query_public('Ticker', {'pair' : pairstr})
            if 0 != len(j['error']):
                raise Exception(j['error'])
            ask = Decimal(j['result'][pairstr]['a'][0])
            bid = Decimal(j['result'][pairstr]['b'][0])
            self.updateRates(p, ask, bid, None)
            res[p] = self.rates[p]
        return res

    def websocket(self):
        """Kraken do not provide websocket API 2018-06-27."""
        return None

    class KrakenTrading(Trading):
        def __init__(self, service):
            self.service = service
            self._lastbalance = None
        def setkeys(self, apikey, apisecret):
            """Add the user specific information required by the trading API in
clear text to the current configuration.  These settings can also be
loaded from the stored configuration.

            """
            self.service.confset('apikey', apikey)
            self.service.confset('apisecret', apisecret)
        async def balance(self):
            """Fetch balance and restructure it to standardized return format,
using standard currency codes.  The return format is a hash with
currency code as the key, and a Decimal() value representing the
current balance.

This is example output from the API call:

{'error': [], 'result': {'KFEE': '0.00', 'BCH': '0.1', 'ZEUR': '1.234', 'XXLM': '1.32', 'XXBT': '1.24'}}

        """
            # Return cached balance if available and less then 10
            # seconds old to avoid triggering rate limit.
            if self._lastbalance is not None and \
               self._lastbalance['_timestamp'] + 10 > time.time():
                return self._lastbalance
            assets = await self.service._query_private('Balance', {})
            res = {}
            for asset in assets.keys():
                c = self.service._revCurrencyMap(asset)
                res[c] = Decimal(assets[asset])
            self._lastbalance = res
            self._lastbalance['_timestamp'] = time.time()
            return res
        async def placeorder(self, marketpair, side, price, volume, immediate=False):
            # Invalidate balance cache
            self._lastbalance = None
            pairstr = self.service._makepair(marketpair[0], marketpair[1])
            if price is None:
                ordertype = 'market'
            else:
                ordertype = 'limit'
            type = {
                    Orderbook.SIDE_ASK : 'sell',
                    Orderbook.SIDE_BID : 'buy',
            }[side]
            args = {
                'pair' : pairstr,
                'type' : type,
                'ordertype' : ordertype,
                'price' : price,
                'volume' : volume,
#                'oflags' : ,
#                'starttm' : ,
            }
            res = await self.service._query_private('AddOrder', args)
            print(res)
            txids = res['txid']
            txdesc = res['descr']
            return txids
        async def cancelorder(self, marketpair, orderref):
            # Invalidate balance cache
            self._lastbalance = None
            args = {'txid' : orderref}
            res = await self.service._query_private('CancelOrder', args)
            return res
        async def cancelallorders(self, marketpair):
            raise NotImplementedError()
        async def orders(self, marketpair = None):
            """Return the currently open orders in standardized format.

FIXME The format is yet to be standardized.
"""
            args = {
                'trades' : True,
#                'userref' : ,
            }
            res = await self.service._query_private('OpenOrders', args)
            print(res)
        def estimatefee(self, side, price, volume):
            """From https://www.kraken.com/help/fees, the max fee is 0.26%.

            """
            return price * volume * Decimal(0.0026)
    def trading(self):
        if self.confget('apikey', fallback=None) is None:
            return None
        if self.activetrader is None:
            self.activetrader = self.KrakenTrading(self)
        return self.activetrader

class TestKraken(unittest.TestCase):
    """
Run simple self test.
"""
    def setUp(self):
        self.s = Kraken(['BTC', 'EUR', 'NOK', 'USD'])
        configpath = expanduser('~/.config/valutakrambod/testsuite.ini')
        self.config = configparser.ConfigParser()
        self.config.read(configpath)
        self.s.confinit(self.config)
        self.ioloop = tornado.ioloop.IOLoop.current()
    def checkTimeout(self):
        print("check timed out")
        self.ioloop.stop()
    def runCheck(self, check):
        to = self.ioloop.call_later(30, self.checkTimeout)
        self.ioloop.add_callback(check)
        self.ioloop.start()
        self.ioloop.remove_timeout(to)
    async def checkFetchTicker(self):
        res = await self.s._fetchTicker()
        pair = ('BTC', 'EUR')
        self.assertTrue(pair in res)
        self.ioloop.stop()
    def testFetchTicker(self):
        self.runCheck(self.checkFetchTicker)
    async def checkFetchOrderbooks(self):
        pairs = self.s.ratepairs()
        await self.s._fetchOrderbooks(pairs)
        for pair in pairs:
            self.assertTrue(pair in self.s.rates)
            self.assertTrue(pair in self.s.orderbooks)
            ask = self.s.rates[pair]['ask']
            bid = self.s.rates[pair]['bid']
            self.assertTrue(ask >= bid)
            spread = 100*(ask/bid-1)
            self.assertTrue(spread > 0 and spread < 5)
        self.ioloop.stop()
    def testFetchOrderbooks(self):
        self.runCheck(self.checkFetchOrderbooks)
    async def checkTradingConnection(self):
        # Unable to test without API access credentials in the config
        if self.s.confget('apikey', fallback=None) is None:
            print("no apikey for %s in ini file, not testing trading" %
                  self.s.servicename())
            self.ioloop.stop()
            return
        t = self.s.trading()

        pair = ('BTC', 'EUR')
        pairstr = self.s._makepair(pair[0], pair[1])
        o = await t.orders()
        print(o)

        rates = await self.s.currentRates()
        ask = rates[pair]['ask']
        bid = rates[pair]['bid']
        askprice = ask * Decimal(1.5) # place test order 50% above current ask price
        bidprice = bid * Decimal(0.5) # place test order 50% below current bid price
        #print("Ask %s -> %s" % (ask, askprice))
        #print("Bid %s -> %s" % (bid, bidprice))

        balance = 0
        bidamount = Decimal('0.01')
        b = await t.balance()
        if pair[1] in b:
            balance = b[pair[1]]
        if balance > bidamount:
            print("placing buy order %s %s at %s %s" % (bidamount, pair[0], bidprice, pair[1]))
            txs = await t.placeorder(pair, Orderbook.SIDE_BID,
                                     bidprice, bidamount, immediate=False)
            print("placed orders: %s" % txs)
            for tx in txs:
                print("cancelling order %s" % tx)
                j = await t.cancelorder(pairstr, tx)
                print("done cancelling: %s" % str(j))
                self.assertTrue('count' in j and j['count'] == 1)
        else:
            print("unable to place %s %s order, balance only had %s"
                  % (bidamount, pair[1], balance))

        balance = 0
        askamount = Decimal('0.001')
        b = await t.balance()
        if pair[0] in b:
            balance = b[pair[0]]
        if balance > askamount:
            print("placing sell order %s %s at %s %s" % (askamount, pair[0], askprice, pair[1]))
            txs = await t.placeorder(pair, Orderbook.SIDE_ASK,
                                     askprice, askamount, immediate=False)
            print("placed orders: %s" % txs)
            for tx in txs:
                print("cancelling order %s" % tx)
                j = await t.cancelorder(pairstr, tx)
                print("done cancelling: %s" % str(j))
                self.assertTrue('count' in j and j['count'] == 1)
        else:
            print("unable to place %s %s order, balance only had %s"
                  % (askamount, pair[0], balance))

        self.ioloop.stop()
    def testTradingConnection(self):
        self.runCheck(self.checkTradingConnection)

    async def checkBalanceCaching(self):
        t = self.s.trading()
        b1 = await t.balance()
        b2 = await t.balance()
        self.assertTrue(b1['_timestamp'] == b2['_timestamp'])
        self.ioloop.stop()
    def testBalanceCaching(self):
        self.runCheck(self.checkBalanceCaching)

if __name__ == '__main__':
    t = TestKraken()
    unittest.main()
