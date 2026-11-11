import tempfile
import unittest
from pathlib import Path

from tapwire.events import DropReason, EventKind
from tapwire.models import PlaybackStatus as S
from tapwire.session import RecordingSession
from tapwire.settings import PauseMode, Settings

from .fakes import FakeClock, FakeEncoder, FakePlayer, FakeRecorder, track


class SessionTestCase(unittest.TestCase):
    pause_mode = PauseMode.DROP

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock = FakeClock()
        self.player = FakePlayer(track("start"), S.PLAYING)  # already playing at startup...
        self.player.pos = 50.0                               # ...and well into the track
        self.recorders, self.encoder, self.events = [], FakeEncoder(), []

        def factory(part):
            self.recorders.append(FakeRecorder(part))
            return self.recorders[-1]

        settings = Settings(player="x", out_dir=Path(self._tmp.name), pause_mode=self.pause_mode)
        self.session = RecordingSession(settings, self.player, factory, self.encoder, clock=self.clock)
        self.session.events.connect(self.events.append)
        self.session.start()

    def kinds(self):
        return [e.kind for e in self.events]

    def reasons(self):
        return [e.reason for e in self.events if e.kind is EventKind.DROPPED]

    def play(self, tid, length=100.0):
        self.player.set(S.PLAYING, track(tid, length))


class BasicTests(SessionTestCase):
    def test_in_progress_track_is_ignored(self):
        self.assertEqual(self.kinds(), [EventKind.READY])
        self.assertIsNone(self.session.current_take)

    def test_records_next_track_and_saves_on_natural_end(self):
        self.play("A")
        self.assertEqual(self.kinds()[-1], EventKind.RECORDING)
        self.clock.advance(99)  # >= 100 - 3
        self.play("B")
        self.assertEqual(self.kinds()[-3:], [EventKind.RECORDING, EventKind.SAVED, EventKind.RECORDING])
        self.assertEqual(len(self.encoder.encoded), 1)

    def test_skip_mid_track_drops(self):
        self.play("A")
        self.clock.advance(30)
        self.play("B")
        self.assertEqual(self.reasons(), [DropReason.TRACK_CHANGED])
        self.assertFalse(self.encoder.encoded)
        self.assertFalse((Path(self._tmp.name) / "Artista - A.part.wav").exists())  # partial file deleted
        self.assertIsNotNone(self.session.current_take)  # and B has already started recording

    def test_playlist_end_saves(self):
        self.play("A")
        self.clock.advance(100)
        self.player.set(S.STOPPED)
        self.assertEqual(self.kinds()[-1], EventKind.SAVED)

    def test_stopped_early_drops(self):
        self.play("A")
        self.clock.advance(10)
        self.player.set(S.STOPPED)
        self.assertEqual(self.reasons(), [DropReason.PLAYER_STOPPED])

    def test_track_changed_while_paused_records_after_play(self):
        self.player.set(S.PAUSED)
        self.player.set(track=track("A"))  # change while paused
        self.assertIsNone(self.session.current_take)
        self.player.set(S.PLAYING)
        self.assertIsNotNone(self.session.current_take)

    def test_stop_discards_current(self):
        self.play("A")
        self.session.stop()
        self.assertEqual(self.reasons(), [DropReason.SHUTDOWN])
        self.assertEqual(self.kinds()[-1], EventKind.STOPPED)

    def test_player_missing_warns(self):
        self.session.stop()
        self.player.available = False
        self.events.clear()
        self.session.start()
        self.assertIn(EventKind.WARNING, self.kinds())

    def test_encoder_failure_emits_error(self):
        self.encoder.ok = False
        self.play("A")
        self.clock.advance(100)
        self.play("B")
        self.assertIn(EventKind.ERROR, self.kinds())


class PauseDropTests(SessionTestCase):
    def test_pause_drops_and_resume_does_not_rerecord(self):
        self.play("A")
        self.clock.advance(10)
        self.player.pos = 10.0  # paused in the middle of the track
        self.player.set(S.PAUSED)
        self.assertEqual(self.reasons(), [DropReason.PAUSED])
        self.player.set(S.PLAYING)  # resuming does not re-record the remainder
        self.assertIsNone(self.session.current_take)

    def test_pause_near_end_saves(self):
        self.play("A")
        self.clock.advance(99)
        self.player.set(S.PAUSED)
        self.assertEqual(self.kinds()[-1], EventKind.SAVED)


class SeekTests(SessionTestCase):
    def test_seek_drops(self):
        self.play("A")
        self.clock.advance(10)
        self.player.seeked.emit(50.0)
        self.assertEqual(self.reasons(), [DropReason.SEEKED])

    def test_seeked_matching_playback_position_is_ignored(self):
        self.play("A")
        self.clock.advance(10)
        self.player.seeked.emit(10.5)  # player re-announcing where it already is
        self.assertEqual(self.reasons(), [])
        self.assertIsNotNone(self.session.current_take)

    def test_seeked_after_track_is_complete_keeps_take(self):
        self.play("A", length=10)
        self.clock.advance(9)
        self.player.seeked.emit(0.0)  # e.g. position reset at the end
        self.assertEqual(self.reasons(), [])

    def test_early_seeked_is_ignored(self):
        self.play("A")
        self.clock.advance(0.5)
        self.player.seeked.emit(0.0)
        self.assertIsNotNone(self.session.current_take)


class PauseJoinTests(SessionTestCase):
    pause_mode = PauseMode.JOIN

    def test_pause_time_does_not_count(self):
        self.play("A", length=10)
        self.clock.advance(4)
        self.player.set(S.PAUSED)
        self.assertTrue(self.session.current_take.paused)
        self.clock.advance(60)  # paused for a long time
        self.player.set(S.PLAYING)
        self.assertFalse(self.session.current_take.paused)
        self.assertAlmostEqual(self.session.current_take.elapsed, 4)
        self.assertEqual(self.recorders[-1].calls, ["start", "pause", "resume"])
        self.clock.advance(6)
        self.play("B")
        self.assertEqual(self.encoder.encoded[0][2].track_id, "/t/A")


if __name__ == "__main__":
    unittest.main()


class CoverNameTests(unittest.TestCase):
    UUID4 = r"\.cover-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"

    def make(self, title):
        from tapwire.take import Take
        final = Path("/tmp/out") / (title + ".flac")
        t = Take(track("x", title=title), final, lambda part: None)
        self.assertRegex(t.art_base.name, "^" + self.UUID4 + "$")
        self.assertEqual(t.art_base.parent, final.parent)
        self.assertIsNone(t.art)  # nothing downloaded yet
        return t

    def test_cover_name_is_random_uuid4_not_derived_from_track(self):
        a, b = self.make("Song ../../evil"), self.make("Song ../../evil")
        self.assertNotIn("Song", a.art_base.name)
        self.assertNotEqual(a.art_base, b.art_base)  # a new name per take

    def test_cover_arriving_after_the_take_ended_is_deleted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            from tapwire.take import Take
            t = Take(track("x"), Path(d) / "a.flac", lambda part: None)
            late = Path(d) / ".cover-late.jpg"
            late.write_bytes(b"\xff\xd8\xff")
            t.close()           # take finished/dropped...
            t.set_art(late)     # ...then the slow download completes
            self.assertFalse(late.exists())


class ContainmentTests(SessionTestCase):
    def test_symlink_planted_in_out_dir_is_refused(self):
        out = Path(self._tmp.name)
        (out / "Artista - A.flac").symlink_to(out.parent / "elsewhere.flac")  # dangling: exists() is False
        self.play("A")
        self.assertIsNone(self.session.current_take)
        self.assertIn(EventKind.ERROR, self.kinds())


class MissingToolTests(SessionTestCase):
    def test_missing_recorder_tool_is_an_error_event_not_a_crash(self):
        from tapwire.tools import ToolNotFound

        def broken(part):
            raise ToolNotFound("'pw-record' not found")

        self.session._recorder_factory = broken
        self.play("A")
        self.assertIn(EventKind.ERROR, self.kinds())
        self.assertIsNone(self.session.current_take)


class RestartFromStartTests(SessionTestCase):
    """Zeroing a track and pressing play records it, since it now plays from the beginning."""

    def zero_and_play(self, status_before=S.PAUSED):
        self.player.pos = 0.0
        self.player.seeked.emit(0.0)           # the user zeroed the track (while paused)
        self.player.set(S.PLAYING)             # ...and pressed play

    def test_dropped_by_pause_then_zero_and_play_records_again(self):
        self.play("A")
        self.clock.advance(10)
        self.player.pos = 10.0
        self.player.set(S.PAUSED)
        self.assertEqual(self.reasons(), [DropReason.PAUSED])
        self.player.set(S.PAUSED)
        self.zero_and_play()
        self.assertIsNotNone(self.session.current_take)
        self.assertEqual(self.kinds().count(EventKind.RECORDING), 2)

    def test_plain_resume_from_the_middle_still_does_not_record(self):
        self.play("A")
        self.clock.advance(10)
        self.player.pos = 10.0
        self.player.set(S.PAUSED)
        self.player.set(S.PLAYING)  # resumes at 10 s
        self.assertIsNone(self.session.current_take)

    def test_track_already_playing_at_startup_records_after_restart(self):
        self.assertIsNone(self.session.current_take)  # in-progress track ignored...
        self.player.pos = 0.4
        self.player.seeked.emit(0.4)                  # ...until it is restarted from the beginning
        self.assertIsNotNone(self.session.current_take)

    def test_zeroing_while_recording_restarts_the_take(self):
        self.play("A")
        self.clock.advance(30)
        self.player.pos = 0.0
        self.player.seeked.emit(0.0)
        self.assertEqual(self.reasons(), [DropReason.SEEKED])
        self.assertIsNotNone(self.session.current_take)
        self.assertEqual(self.kinds().count(EventKind.RECORDING), 2)

    def test_seek_elsewhere_does_not_start_a_recording(self):
        self.player.pos = 60.0
        self.player.seeked.emit(60.0)
        self.assertIsNone(self.session.current_take)

    def test_seek_to_start_while_paused_does_not_record_until_play(self):
        self.player.set(S.PAUSED)
        self.player.pos = 0.0
        self.player.seeked.emit(0.0)
        self.assertIsNone(self.session.current_take)
        self.player.set(S.PLAYING)
        self.assertIsNotNone(self.session.current_take)

    def test_unknown_position_never_starts_a_recording(self):
        self.player.position = lambda: None
        self.player.set(S.PAUSED)
        self.player.set(S.PLAYING)
        self.assertIsNone(self.session.current_take)


class StartupTests(unittest.TestCase):
    """What the session does with the track that is playing the moment it is switched on."""

    def start(self, status=S.PLAYING, pos=50.0, current=track("now")):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        player = FakePlayer(current, status)
        player.pos = pos
        self.events, self.recorders = [], []
        session = RecordingSession(Settings(player="x", out_dir=Path(self._tmp.name)), player,
                                   lambda part: (self.recorders.append(FakeRecorder(part)) or self.recorders[-1]),
                                   FakeEncoder(), clock=FakeClock())
        session.events.connect(self.events.append)
        session.start()
        self.session = session
        return [e.kind for e in self.events]

    def test_playing_midway_is_skipped_and_named_in_ready(self):
        self.assertEqual(self.start(pos=50.0), [EventKind.READY])
        self.assertIsNotNone(self.events[0].track)  # the message says which track is being skipped
        self.assertIsNone(self.session.current_take)

    def test_playing_from_the_very_start_is_recorded_at_once(self):
        self.assertEqual(self.start(pos=0.8), [EventKind.READY, EventKind.RECORDING])
        self.assertIsNone(self.events[0].track)     # nothing is being skipped, so READY names no track
        self.assertIsNotNone(self.session.current_take)

    def test_paused_track_is_not_called_in_progress(self):
        self.assertEqual(self.start(status=S.PAUSED, pos=0.0), [EventKind.READY])
        self.assertIsNone(self.events[0].track)
        self.assertIsNone(self.session.current_take)

    def test_nothing_playing(self):
        self.assertEqual(self.start(status=S.STOPPED, current=None), [EventKind.READY])
        self.assertIsNone(self.events[0].track)

    def test_unknown_position_counts_as_midway(self):
        player_pos = lambda: None
        self.start(pos=0.0)  # builds a session; now redo with an unknown position
        self.session.stop()
        self.session.player.position = player_pos
        self.events.clear()
        self.session.start()
        self.assertEqual([e.kind for e in self.events], [EventKind.READY])
        self.assertIsNotNone(self.events[0].track)


class TrackLimitTests(unittest.TestCase):
    def make(self, limit):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.clock, self.encoder, self.events = FakeClock(), FakeEncoder(), []
        self.player = FakePlayer(track("start"), S.PLAYING)
        self.player.pos = 50.0
        settings = Settings(player="x", out_dir=Path(self._tmp.name), max_tracks=limit)
        self.session = RecordingSession(settings, self.player, lambda part: FakeRecorder(part), self.encoder,
                                        clock=self.clock)
        self.session.events.connect(self.events.append)
        self.session.start()

    def full_track(self, tid):
        self.player.set(S.PLAYING, track(tid, 100.0))
        self.clock.advance(99)

    def test_stops_after_n_saved_and_does_not_start_another(self):
        self.make(2)
        self.full_track("A")
        self.full_track("B")
        self.player.set(S.PLAYING, track("C", 100.0))  # B saved here -> limit reached
        kinds = [e.kind for e in self.events]
        self.assertEqual(kinds.count(EventKind.SAVED), 2)
        self.assertEqual(kinds[-2:], [EventKind.LIMIT, EventKind.STOPPED])
        self.assertIsNone(self.session.current_take)
        self.assertTrue(self.session.finished_by_limit)

    def test_zero_means_manual(self):
        self.make(0)
        for tid in "ABCD":
            self.full_track(tid)
        self.assertNotIn(EventKind.LIMIT, [e.kind for e in self.events])

    def test_negative_limit_rejected(self):
        with self.assertRaises(ValueError):
            Settings(max_tracks=-1)
