"""Deployment routing must preserve the process-local speech owner."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'voice_topology', Path(__file__).with_name('check-voice-topology.py')
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def topology(mode='Multiple', replicas=1, sticky=None, workers='1', traffic=None):
    """Minimal saved Container App show response, without credentials."""
    return {
        'properties': {
            'configuration': {
                'activeRevisionsMode': mode,
                'ingress': {
                    'transport': 'Auto',
                    'stickySessions': {'affinity': sticky},
                    'traffic': traffic
                    or [{'revisionName': 'candidate', 'weight': 100}],
                },
            },
            'template': {
                'scale': {'maxReplicas': replicas},
                'containers': [
                    {'env': [{'name': 'INVENTREE_GUNICORN_WORKERS', 'value': workers}]}
                ],
            },
        }
    }


class VoiceTopologyTest(unittest.TestCase):
    """Distinguish safe affinity from the observed multi-replica topology."""

    def test_campaign_topology_is_rejected(self):
        """The exact failed campaign's deployment must not pass the gate."""
        self.assertTrue(guard.validate(topology(replicas=2)))

    def test_supported_routing_and_worker_boundaries(self):
        """Only documented affinity and process-count combinations pass."""
        for document in [topology(), topology('Single', 2, 'sticky')]:
            with self.subTest(document=document):
                self.assertEqual(guard.validate(document), [])
        for document in [
            topology('Multiple', 2, 'sticky'),
            topology('Single', 2),
            topology(workers='2'),
            topology(workers=None),
            topology(traffic=[{'weight': 50}, {'weight': 50}]),
        ]:
            with self.subTest(document=document):
                self.assertTrue(guard.validate(document))


if __name__ == '__main__':
    unittest.main()
