# -*- coding: utf-8 -*-
# Copyright (c) 2018 Petter Reinholdtsen <pere@hungry.com>
# This file is covered by the GPLv2 or later, read COPYING for details.


import configparser
import dateutil
import datetime
import unittest

from os.path import expanduser
from pytz import UTC

from valutakrambod.services import Service

class OneForge(Service):
    """Query the 1 Forge API. Documentation is available from
https://1forge.com/forex-data-api .

    """
    baseurl = "https://forex.1forge.com/1.0.1/"

    def servicename(self):
        return "OneForge"

    def ratepairs(self):
        return [
            ('EUR', 'NOK'),
            ('USD', 'EUR'),
            ('USD', 'NOK'),
            ]
    def fetchRates(self, pairs = None):
        apikey = self.confget('apikey', fallback=None)
        if apikey is None:
            raise Exception('1Forge require API key')
        if pairs is None:
            pairs = self.ratepairs()
        #print(pairs)
        pairstr = ','.join(map(lambda t: "%s%s" % (t[0], t[1]), pairs))
        url = "%squotes?pairs=%s&api_key=%s" % (self.baseurl, pairstr, apikey)
        #print(url)
        j, r = self._jsonget(url)
        #print(j)
        res = {}
        for r in j:
            pair = (r['symbol'][:3], r['symbol'][3:])
            if pair not in self.ratepairs():
                continue
            self.updateRates(pair,
                             r['ask'],
                             r['bid'],
                             r['timestamp'],
            )
            res[pair] = self.rates[pair]
        return res

    def websocket(self):
        """Websocket API not yet implemented 2018-07-03."""
        return None

class TestOneForge(unittest.TestCase):
    """
Run simple self test.
"""
    def setUp(self):
        self.s = OneForge(['BTC', 'EUR', 'NOK', 'USD'])
        configpath = expanduser('~/.config/valutakrambod/testsuite.ini')
        self.config = configparser.ConfigParser()
        self.config.read(configpath)
        self.s.confinit(self.config)
    def testFetchTicker(self):
        res = self.s.fetchRates()
        for pair in self.s.ratepairs():
            self.assertTrue(pair in res)

if __name__ == '__main__':
    t = TestOneForge()
    unittest.main()
