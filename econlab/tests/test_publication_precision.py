"""A paper rounding allowance never conceals the original strict error."""
import copy, sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import runner


class PublicationPrecision(unittest.TestCase):
    def policy(self):
        return {'rules':[{'field':'y','index':0,'decimals':2}],
                'reason':'User accepts the two-decimal published rounding interval',
                'authorized_by':'User instruction', 'evidence':[{'page':20,'quote':'Table 2'}]}

    def test_default_strict_failure_is_preserved(self):
        self.assertFalse(runner.compare({'y':[.0442859]},{'y':[.04]},['y'])['passed'])

    def test_authorized_rounding_pass_keeps_raw_error(self):
        r=runner.compare({'y':[.0442859]},{'y':[.04]},['y'],publication_precision=self.policy())
        self.assertTrue(r['passed']);self.assertFalse(r['strict_passed'])
        self.assertEqual(r['publication_precision_acceptance_count'],1)
        self.assertGreater(r['metrics'][0]['max_relative_error'],.10)
        self.assertEqual(r['metrics'][0]['publication_precision_acceptances'][0]['actual'],.0442859)

    def test_outside_rounding_interval_still_fails(self):
        self.assertFalse(runner.compare({'y':[.046]},{'y':[.04]},['y'],publication_precision=self.policy())['passed'])

    def test_only_authorized_element_gets_exception(self):
        self.assertFalse(runner.compare({'y':[.0442859,.0442859]},{'y':[.04,.04]},['y'],publication_precision=self.policy())['passed'])

    def test_zero_reference_still_needs_absolute_tolerance(self):
        self.assertFalse(runner.compare({'y':[.001]},{'y':[0]},['y'],publication_precision=self.policy())['passed'])

    def test_policy_and_reference_digits_are_checked(self):
        for value in (-1,True,13):
            p=self.policy();p['rules'][0]['decimals']=value
            with self.assertRaises(ValueError):runner.compare({'y':[.04]},{'y':[.04]},['y'],publication_precision=p)
        p=self.policy();p['rules'][0]['index']=2
        with self.assertRaises(ValueError):runner.compare({'y':[.04]},{'y':[.04]},['y'],publication_precision=p)
        with self.assertRaises(ValueError):runner.compare({'y':[.041]},{'y':[.041]},['y'],publication_precision=self.policy())
        p=self.policy();p['authorized_by']=''
        with self.assertRaises(ValueError):runner.compare({'y':[.04]},{'y':[.04]},['y'],publication_precision=p)


if __name__=='__main__':unittest.main()
