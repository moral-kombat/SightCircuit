import unittest

from sightcircuit.sources.replay import PlaybackClock


class PlaybackClockTests(unittest.TestCase):
    def setUp(self):
        self.wall = 100.0
        self.clock = PlaybackClock(lambda: self.wall)

    def test_first_frame_defines_origin(self):
        self.assertEqual(self.clock.lateness(5.0), 0.0)

    def test_on_time_early_and_late(self):
        self.clock.lateness(1.0)
        self.wall += 0.5
        self.assertAlmostEqual(self.clock.lateness(1.5), 0.0)
        self.assertAlmostEqual(self.clock.lateness(1.75), -0.25)
        self.wall += 1.0
        self.assertAlmostEqual(self.clock.lateness(1.6), 0.9)

    def test_rebase_restarts_schedule(self):
        self.clock.lateness(0.0)
        self.wall += 10
        self.clock.rebase()
        self.assertEqual(self.clock.lateness(3.0), 0.0)


if __name__ == "__main__":
    unittest.main()
