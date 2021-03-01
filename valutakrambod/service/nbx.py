# -*- coding: utf-8 -*-
# Copyright (c) 2021 Petter Reinholdtsen <pere@hungry.com>
# This file is covered by the GPLv2 or later, read COPYING for details.

import unittest
import tornado.ioloop

from decimal import Decimal

from valutakrambod.services import Orderbook
from valutakrambod.services import Service

class Nbx(Service):
    """Query the Norwegian Block Exchange AS API.  Based on documentation
found in https://nbx.com/developers .
    """
    baseurl = "https://api.nbx.com/"

    def servicename(self):
        return "NBX"

    def ratepairs(self):
        return [
            ('BTC', 'NOK'),
            ]
    async def fetchRates(self, pairs = None):
        if pairs is None:
            pairs = self.wantedpairs
        await self.fetchOrderbooks(pairs)

    async def fetchOrderbooks(self, pairs):
        for pair in pairs:
            o = Orderbook()
            url = "%smarkets/%s-%s/orders" % (self.baseurl, pair[0], pair[1])
            #print(url)
            j, r = await self._jsonget(url)
            #print(j)
            for order in j:
                oside = {
                    'BUY': o.SIDE_BID,
                    'SELL' : o.SIDE_ASK,
                }[order['side']]
                o.update(oside, Decimal(order['price']), Decimal(order['quantity']))
                #print(o)
            self.updateOrderbook(pair, o)

    def websocket(self):
        """NBX do not seem to provide websocket API 2021-02-27."""
        return None

class TestNbx(unittest.TestCase):
    """Simple self test.

    """
    def setUp(self):
        self.s = Nbx(['BTC', 'NOK'])
        self.ioloop = tornado.ioloop.IOLoop.current()
    def checkTimeout(self):
        print("check timed out")
        self.ioloop.stop()
    def runCheck(self, check):
        to = self.ioloop.call_later(30, self.checkTimeout)
        self.ioloop.add_callback(check)
        self.ioloop.start()
        self.ioloop.remove_timeout(to)
    async def checkCurrentRates(self):
        res = await self.s.currentRates()
        for pair in self.s.ratepairs():
            self.assertTrue(pair in res)
            ask = res[pair]['ask']
            bid = res[pair]['bid']
            self.assertTrue(ask >= bid)
            spread = 100*(ask/bid-1)
            print("Spread for %s:" % str(pair), spread)
            self.assertTrue(0 < spread and spread < 100)
        self.ioloop.stop()
    def testCurrentRates(self):
        self.runCheck(self.checkCurrentRates)
    async def checkCompareOrderbookTicker(self):
        # Try to detect when the two ways to fetch the ticker disagree
        asks = {}
        bids = {}
        laststore = newstore = 0
        res = await self.s.fetchOrderbooks(self.s.wantedpairs)
        for pair in self.s.wantedpairs:
            asks[pair] = self.s.rates[pair]['ask']
            bids[pair] = self.s.rates[pair]['bid']
            if laststore < self.s.rates[pair]['stored']:
                laststore = self.s.rates[pair]['stored']
        res = await self.s.fetchRates(self.s.wantedpairs)
        for pair in self.s.wantedpairs:
            if asks[pair] != self.s.rates[pair]['ask']:
                print("ask order book (%.1f and ticker (%.1f) differ for %s" % (
                    asks[pair],
                    self.s.rates[pair]['ask'],
                    pair
                ))
                self.assertTrue(False)
            if bids[pair] != self.s.rates[pair]['bid']:
                print("bid order book (%.1f and ticker (%.1f) differ for %s" % (
                    bids[pair],
                    self.s.rates[pair]['bid'],
                    pair
                ))
                self.assertTrue(False)
            if newstore < self.s.rates[pair]['stored']:
                newstore = self.s.rates[pair]['stored']
            #print(laststore, newstore)
            self.assertTrue(laststore != newstore)
        self.ioloop.stop()
    def testCompareOrderbookTicker(self):
        self.runCheck(self.checkCompareOrderbookTicker)

if __name__ == '__main__':
    t = TestNbx()
    unittest.main()
