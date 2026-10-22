"""Checks for dangerous quote-unit, identity, freshness and fallback errors."""
import copy
import unittest
from datetime import timedelta

from fetch_markets import SPECS,make_quote,now,parse_em,parse_sina,choose_quote


class Quotes(unittest.TestCase):
    def spec(self,key):
        return next(x for x in SPECS if x['key']==key)

    def test_bond_decimal_and_basis_points(self):
        data={'rc':0,'data':{'f43':51231,'f60':51147,'f57':'US10Y','f58':'美国10年期国债收益率','f59':4,'f86':now().timestamp(),'f170':16}}
        q=parse_em(self.spec('us10y'),data)
        self.assertAlmostEqual(q['price'],5.1231)
        self.assertAlmostEqual(q['change_bp'],.84)
        self.assertAlmostEqual(q['change_pct'],.1642325063)

    def test_wrong_nasdaq_index_is_rejected(self):
        bad={'rc':0,'data':{'f43':2949028,'f60':2952934,'f57':'NDX','f58':'纳斯达克100','f59':2,'f86':now().timestamp(),'f170':-13}}
        with self.assertRaisesRegex(ValueError,'Nasdaq'):
            parse_em(self.spec('nasdaq'),bad)

    def test_wrong_percent_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'percent'):
            make_quote(self.spec('mags'),72.2,72.47,now().isoformat(),'test','https://example.com',reported_pct=37)

    def test_zero_reference_is_not_reported_as_zero_change(self):
        with self.assertRaises(ValueError):
            make_quote(self.spec('gold'),4119,0,now().isoformat(),'test','https://example.com')

    def test_future_quote_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'future'):
            make_quote(self.spec('mags'),72.2,72.47,(now()+timedelta(days=1)).isoformat(),'test','https://example.com')

    def test_stale_crypto_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'stale'):
            make_quote(self.spec('btc'),82600,82000,(now()-timedelta(minutes=20)).isoformat(),'test','https://example.com')

    def test_spot_gold_cannot_be_futures(self):
        vals=['4119','','','','','','11:00:00','4125','','','','',now().date().isoformat(),'黄金期货']
        with self.assertRaisesRegex(ValueError,'mismatch'):
            parse_sina(self.spec('gold'),{'hf_XAU':vals})

    def test_independent_fallback_and_conflict(self):
        s=self.spec('mags')
        q=make_quote(s,72.2,72.47,now().isoformat(),'backup','https://example.com')
        selected=choose_quote(s,None,copy.deepcopy(q))
        self.assertTrue(selected['fallback_used'])
        bad=copy.deepcopy(q)
        bad['price']=90
        with self.assertRaisesRegex(ValueError,'conflict'):
            choose_quote(s,q,bad)


if __name__=='__main__':
    unittest.main()
