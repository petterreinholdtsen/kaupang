# -*- coding: utf-8 -*-
# Copyright (c) 2018 Petter Reinholdtsen <pere@hungry.com>
# This file is covered by the GPLv2 or later, read COPYING for details.

import base64
import configparser
import hashlib
import json
import hmac
import time
import unittest
import urllib

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
        'BTC' : 'XBT',
        }
    baseurl = "https://api.kraken.com/0/public/"
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
    def _makepair(self, f, t):
        return "X%sZ%s" % (self._currencyMap(f), self._currencyMap(t))
    def fetchRates(self, pairs = None):
        if pairs is None:
            pairs = self.wantedpairs
        #self._fetchTicker(pairs)
        self._fetchOrderbooks(pairs)

    def _fetchOrderbooks(self, pairs):
        now = time.time()
        res = {}
        for pair in pairs:
            pairstr = self._makepair(pair[0], pair[1])
            url = "%sDepth?pair=%s" % (self.baseurl, pairstr)
            #print(url)
            j, r = self._jsonget(url)
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
                    o.update(oside, float(order[0]), float(order[1]), order[2])
                #print(o)
            self.updateOrderbook(pair, o)

    def _fetchTicker(self, pairs = None):
        if pairs is None:
            pairs = self.ratepairs()
        res = {}
        for p in pairs:
            f = p[0]
            t = p[1]
            pair= self._makepair(f, t)
            #print(pair)
            url = "%sTicker?pair=%s" % (self.baseurl, pair)
            #print(url)
            j, r = self._jsonget(url)
            #print(j)
            if 0 != len(j['error']):
                raise Exception(j['error'])
            ask = float(j['result'][pair]['a'][0])
            bid = float(j['result'][pair]['b'][0])
            self.updateRates(p, ask, bid, None)
            res[p] = self.rates[p]
        return res

    def websocket(self):
        """Kraken do not provide websocket API 2018-06-27."""
        return None

    class KrakenTrading(Trading):
        baseurl = "https://api.kraken.com/0/private/"
        def __init__(self, service):
            self.service = service
        def setkeys(self, apikey, apisecret):
            """Add the user specific information required by the trading API in
clear text to the current configuration.  These settings can also be
loaded from the stored configuration.

            """
            self.service.confset('apikey', apikey)
            self.service.confset('apisecret', apisecret)
        def _nonce(self):
            nonce = self.service.confgetint('lastnonce', fallback=0) + 1
            # Time based alternative
            #nonce = int(1000*time.time())
            nonce = int(time.time())
            return nonce

        def _post(self, url, data):
            urlpath = urllib.parse.urlparse(url).path.encode('UTF-8')
            data['nonce'] = self._nonce()
            datastr = urllib.parse.urlencode(data)

            # API-Sign = Message signature using HMAC-SHA512 of (URI
            # path + SHA256(nonce + POST data)) and base64 decoded
            # secret API key
            noncestr = str(data['nonce'])
            datahash = (noncestr + datastr).encode('UTF-8')
            message = urlpath + hashlib.sha256(datahash).digest()
            msgsignature = hmac.new(base64.b64decode(self.service.confget('apisecret').encode('UTF-8')),
                                    message,
                                    hashlib.sha512)
            sign = base64.b64encode(msgsignature.digest()).replace(b'\n', b'')
            headers = {
                'API-Key' : self.service.confget('apikey'),
                'API-Sign': sign,
                }
            body, response = self.service._post(url, datastr, headers)
            return body, response
        def _query_private(self, method, args):
            url = "%sBalance" % self.baseurl
            body, response = self._post(url, args)
            j = json.loads(body.decode('UTF-8'))
            print(j)
            if 0 != len(j['error']):
                exceptionmap = {
                    'EGeneral:Internal error' : Exception,
                    'EAPI:Invalid nonce' : Exception,
                }
                e = Exception
                if j['error'][0] in exceptionmap:
                    e = exceptionmap[j['error'][0]]
                raise e('unable to fetch balance: %s' % j['error'])
            return j['result']
        def balance(self):
            raise NotImplementedError()
            url = "%sBalance" % self.baseurl
            assets = self._query_private('Balance', {})
            for asset in assets:
                print(asset)
            return assets
        def placeorder(self, marketpair, side, price, volume, immediate=False):
            raise NotImplementedError()
            if prices is None:
                ordertype = 'market'
            else:
                ordertypes = 'limit'
                type = {
                    OrderBook.SIDE_ASK : 'sell',
                    OrderBook.SIDE_BID : 'buy',
                }[side]
            args = {
                'pair' : 'XXBTZUSD',
                'type' : type,
                'ordertype' : ordertype,
                'price' : str(price),
                'volume' : str(volume),
#                'oflags' : ,
#                'starttm' : ,
            }
            res = self._query_private('AddOrder', args)
            txid = res['result']['txid']
            txdesc = res['result']['descr']
            return txid
        def cancelorder(self, orderref):
            raise NotImplementedError()
            args = {'txid' : orderref}
            res = self._query_private('CancelOrder', args)
        def cancelallorders(self):
            raise NotImplementedError()
        def orders(self, market= None):
            raise NotImplementedError()
            args = {
                'trades' : True,
#                'userref' : ,
            }
            res = self._query_private('OpenOrders', args)
            print(res)
    def trading(self):
        if self.trader is None:
            self.trader = self.KrakenTrading(self)
        return self.trader

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
    def testFetchTicker(self):
        res = self.s._fetchTicker()
        pair = ('BTC', 'EUR')
        self.assertTrue(pair in res)
    def testFetchOrderbooks(self):
        pairs = self.s.ratepairs()
        self.s._fetchOrderbooks(pairs)
        for pair in pairs:
            self.assertTrue(pair in self.s.rates)
            self.assertTrue(pair in self.s.orderbooks)
            ask = self.s.rates[pair]['ask']
            bid = self.s.rates[pair]['bid']
            self.assertTrue(ask >= bid)
            spread = 100*(ask/bid-1)
            self.assertTrue(spread > 0 and spread < 5)
    def testTradingConnection(self):
        # Unable to test without API access credentials in the config
        if self.s.confget('apikey', fallback=None) is None:
            return
        t = self.s.trading()
        print(t.balance())
        print(t.orders())
if __name__ == '__main__':
    t = TestKraken()
    unittest.main()
