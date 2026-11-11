"""Main window: 2 columns (controls, settings, recorded + log | player); a single column when narrow."""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GObject, GLib, Gtk  # noqa: E402

from ..describe import describe, player_label  # noqa: E402
from ..events import Event, EventKind  # noqa: E402
from ..i18n import _  # noqa: E402
from ..models import PlaybackStatus  # noqa: E402
from ..player import MprisPlayer  # noqa: E402
from ..session import RecordingSession  # noqa: E402
from ..settings import Settings, default_out_dir  # noqa: E402
from ..text import display_path, printable  # noqa: E402
from .icons import icon_for  # noqa: E402
from .player_view import PlayerView  # noqa: E402

def _markup_safe(text: str) -> str:
    """Libadwaita rows parse markup: escape it (and make control characters visible)."""
    return GLib.markup_escape_text(printable(text))


NARROW = "max-width: 860sp"
LOG_MAX_LINES = 1000
DEFAULT_WIDTH = 560     # px: below the breakpoint, so the app opens in the compact (thumbnail) layout
FALLBACK_HEIGHT = 800   # px: used when no monitor information is available


def _monitor_height() -> int:
    """Height of the monitor, to open as tall as a maximized window.

    The window manager trims a taller request to the usable area (screen minus panels), so asking for the
    full monitor height gives the maximized height without knowing where the panels are.
    """
    display = Gdk.Display.get_default()
    monitor = display.get_monitors().get_item(0) if display else None
    return monitor.get_geometry().height if monitor else FALLBACK_HEIGHT


class PlayerItem(GObject.Object):
    """One entry of the player selector: its label plus the player it stands for (None = placeholder).

    The item carries its own PlayerInfo because GTK does not give a reliable list position for the
    widget that shows the *selected* entry; looking the info up by position left a stale icon there.
    """

    def __init__(self, label: str, info=None) -> None:
        super().__init__()
        self.label, self.info = label, info


def _value(gtype, value) -> GObject.Value:
    v = GObject.Value()
    v.init(gtype)
    if gtype is bool:
        v.set_boolean(value)
    elif gtype is int:
        v.set_int(value)
    elif gtype is float:
        v.set_double(value)
    else:  # enums
        v.set_enum(int(value))
    return v


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="tapwire", default_width=DEFAULT_WIDTH, default_height=_monitor_height())
        self.session: RecordingSession | None = None
        self.out_dir = default_out_dir()
        self._infos: list = []

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)

        # two columns: [recording control, settings, recorded, log] | [player]
        self.col_l, self.col_c = (
            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.START) for _ in range(2))
        # The left column fills the window's height so the log can take whatever is left below the groups.
        self.col_l.set_valign(Gtk.Align.FILL)
        self.main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=24,
                            margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        for column in (self.col_l, self.col_c):
            self.main.append(column)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.main)
        self.page_scroller = scroller
        self.bin = Adw.BreakpointBin(width_request=360, height_request=360, child=scroller)
        view.set_content(self.bin)
        self.set_content(view)

        self._build_player()
        self._build_control()
        self._build_config()
        self._build_saved_and_log()
        self._setup_breakpoint()
        self.connect("close-request", self._on_close)

    # ---------- construction ----------
    def _build_player(self) -> None:
        self.player_view = PlayerView()
        # the player may use the visible height minus the page margins: it scales with the window
        self.player_view.available_height = lambda: (
            self.page_scroller.get_height() - self.main.get_margin_top() - self.main.get_margin_bottom())
        group = Adw.PreferencesGroup()
        group.add(self.player_view)
        self.col_c.append(group)

    def _build_control(self) -> None:
        group = Adw.PreferencesGroup()
        self.col_l.append(group)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("state-set", self._on_switch)
        self.status_row = Adw.ActionRow(title=_("Automatic recording"), subtitle=_("Stopped"))
        self.status_row.add_suffix(self.switch)
        self.status_row.set_activatable_widget(self.switch)
        group.add(self.status_row)

        self.rec_row = Adw.ActionRow(title=_("Recording"), subtitle="—")
        self.rec_dot = Gtk.Image(icon_name="media-record-symbolic", visible=False)
        self.rec_dot.add_css_class("error")
        self.rec_row.add_prefix(self.rec_dot)
        group.add(self.rec_row)

    def _build_config(self) -> None:
        group = Adw.PreferencesGroup(title=_("Settings"))
        self.col_l.append(group)
        self.players = Gio.ListStore(item_type=PlayerItem)
        self.player_row = Adw.ComboRow(title=_("MPRIS player / app"), model=self.players)
        self.player_row.set_factory(self._player_factory(icon_only=True))        # the closed row
        self.player_row.set_list_factory(self._player_factory(icon_only=False))  # the open list
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text=_("Refresh app list"))
        refresh.connect("clicked", lambda *_: self._refresh_players())
        self.player_row.add_suffix(refresh)
        group.add(self.player_row)
        self._refresh_players()
        self.player_row.connect("notify::selected", lambda *_: self._bind_player())

        self.count_row = Adw.SpinRow(
            title=_("Tracks to record"), subtitle=_("∞ = manual, until you stop it"),
            adjustment=Gtk.Adjustment(value=0, lower=0, upper=999, step_increment=1, page_increment=10))
        # 0 means "no limit": show it as ∞. Typing "∞" parses to nothing and keeps the value 0.
        self.count_row.connect("output", self._on_count_output)
        self.count_row.update()
        group.add(self.count_row)

        self.dir_row = Adw.ActionRow(title=_("Output folder"))
        self._show_out_dir()
        pick = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER)
        pick.connect("clicked", self._on_pick_dir)
        self.dir_row.add_suffix(pick)
        group.add(self.dir_row)

    @staticmethod
    def _on_count_output(row: Adw.SpinRow) -> bool:
        value = int(row.get_value())
        row.set_text("∞" if value == 0 else str(value))
        return True  # we formatted the text ourselves

    def _build_saved_and_log(self) -> None:
        self.saved_group = Adw.PreferencesGroup(visible=False)  # untitled; shown once the first track is saved
        self.col_l.append(self.saved_group)
        log_group = Adw.PreferencesGroup(title=_("Log"), vexpand=True)  # grows into the free space below
        # Two plain text buttons in the group header: [Copy logs] [Clear logs]
        actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.copy_btn = Gtk.Button(label=_("Copy logs"))
        self.clear_btn = Gtk.Button(label=_("Clear logs"))
        for button, handler in ((self.copy_btn, self._on_copy_log), (self.clear_btn, self._on_clear_log)):
            button.add_css_class("flat")
            button.connect("clicked", handler)
            actions.append(button)
        log_group.set_header_suffix(actions)
        self.col_l.append(log_group)
        self.log_buf = Gtk.TextBuffer()
        self.log_buf.connect("changed", self._on_log_changed)
        self._on_log_changed(self.log_buf)  # nothing logged yet: both buttons start disabled
        self.log_view = Gtk.TextView(buffer=self.log_buf, editable=False, monospace=True,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6,
                                     left_margin=8, right_margin=8)
        scroller = Gtk.ScrolledWindow(min_content_height=100, vexpand=True, child=self.log_view)
        self._log_follow = True  # stay on the newest line (the top) while new lines arrive, unless the user scrolled down
        adjustment = scroller.get_vadjustment()
        adjustment.connect("value-changed", self._on_log_scrolled)
        adjustment.connect("notify::upper", self._follow_log)
        # Following stops only on an explicit user action (wheel/touchpad down, or grabbing the scrollbar).
        # It must not be inferred from the view's geometry: GTK itself nudges the view while new text is
        # laid out, which looked like "moved away" and switched following off for good.
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_log_wheel)
        scroller.add_controller(wheel)
        grab = Gtk.GestureClick()
        grab.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        grab.connect("pressed", lambda *_: self._stop_following_log())
        scroller.get_vscrollbar().add_controller(grab)
        scroller.add_css_class("card")
        log_group.add(scroller)

    def _setup_breakpoint(self) -> None:
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
        H, V = Gtk.Orientation.HORIZONTAL, Gtk.Orientation.VERTICAL
        bp.add_setter(self.main, "orientation", _value(Gtk.Orientation, V))
        bp.add_setter(self.main, "homogeneous", _value(bool, False))
        bp.add_setter(self.main, "margin-start", _value(int, 12))
        bp.add_setter(self.main, "margin-end", _value(int, 12))
        pv = self.player_view  # the player card becomes horizontal and compact
        bp.add_setter(pv.card, "orientation", _value(Gtk.Orientation, H))
        bp.add_setter(pv.art_clamp, "width-request", _value(int, 112))
        bp.add_setter(pv.art_clamp, "hexpand", _value(bool, False))
        bp.add_setter(pv.art_clamp, "valign", _value(Gtk.Align, Gtk.Align.START))
        for label in pv.labels:
            bp.add_setter(label, "xalign", _value(float, 0.0))

        def to_narrow(*_):  # player first
            pv.compact = True
            self.main.reorder_child_after(self.col_c, None)

        def to_wide(*_):    # controls and lists | player
            pv.compact = False
            self.main.reorder_child_after(self.col_l, None)
            self.main.reorder_child_after(self.col_c, self.col_l)

        bp.connect("apply", to_narrow)
        bp.connect("unapply", to_wide)
        self.bin.add_breakpoint(bp)

    # ---------- players ----------
    def _player_factory(self, icon_only: bool) -> Gtk.SignalListItemFactory:
        """[app icon] label for the entries of the open list; just the [app icon] for the selected entry
        (full text in its tooltip). The "no player found" placeholder has no icon, so it keeps its text."""
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            box = Gtk.Box(spacing=8)
            box.append(Gtk.Image(pixel_size=24 if icon_only else 20))
            box.append(Gtk.Label(xalign=0, ellipsize=3, hexpand=True))
            item.set_child(box)

        def bind(_factory, item):
            box = item.get_child()
            image, label = box.get_first_child(), box.get_last_child()
            entry = item.get_item()
            label.set_label(entry.label)
            info = entry.info
            image.set_visible(info is not None)
            label.set_visible(not icon_only or info is None)
            box.set_tooltip_text(entry.label if icon_only and info else None)
            if info:
                icon = icon_for(info)
                image.set_from_gicon(icon) if isinstance(icon, Gio.Icon) else image.set_from_icon_name(icon)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def _refresh_players(self) -> None:
        current = self._selected_player()
        try:
            self._infos = MprisPlayer.discover()
        except GLib.Error as exc:
            self._log(_("failed to list players: {error}").format(error=exc.message))
            self._infos = []
        items = [PlayerItem(player_label(info), info) for info in self._infos]
        self.players.splice(0, self.players.get_n_items(), items or [PlayerItem(_("(no player found)"))])
        self.player_row.set_sensitive(bool(self._infos))
        names = [info.name for info in self._infos]
        if current in names:
            self.player_row.set_selected(names.index(current))
        else:  # nothing chosen yet: prefer an app that is playing right now
            playing = [i for i, info in enumerate(self._infos) if info.status is PlaybackStatus.PLAYING]
            if playing:
                self.player_row.set_selected(playing[0])
        self._bind_player()

    def _selected_player(self) -> str | None:
        """D-Bus name (not the label) of the selected player."""
        infos = getattr(self, "_infos", [])
        index = self.player_row.get_selected() if hasattr(self, "player_row") else -1
        return infos[index].name if 0 <= index < len(infos) else None

    def _bind_player(self) -> None:
        name = self._selected_player()
        self.player_view.bind(MprisPlayer(name) if name else None)

    # ---------- session ----------
    def _on_switch(self, _switch, state: bool) -> bool:
        if state and not self.session:
            name = self._selected_player()
            if not name:
                self._log(_("no player selected; open the app and click refresh"))
                return True  # blocks the toggle
            self.session = RecordingSession.create(Settings(player=name, out_dir=self.out_dir,
                                                        max_tracks=int(self.count_row.get_value())))
            self.session.events.connect(self._on_event)
            self.session.start()
            self._set_locked(True)
        elif not state and self.session:
            self.session.stop()  # discards the partial in-progress recording
            self.session = None
            self._set_locked(False)
        return False

    def _set_locked(self, locked: bool) -> None:
        self.player_row.set_sensitive(not locked)
        self.dir_row.set_sensitive(not locked)
        self.count_row.set_sensitive(not locked)

    def _on_event(self, event: Event) -> None:
        self._log(describe(event))
        kind = event.kind
        if kind is EventKind.RECORDING:
            self.rec_row.set_subtitle(_markup_safe(event.track.label))
            self.rec_dot.set_visible(True)
            self.status_row.set_subtitle(_("Recording"))
        elif kind is EventKind.PAUSED:
            self.rec_dot.set_visible(False)
            self.status_row.set_subtitle(_("Recording paused"))
        elif kind is EventKind.RESUMED:
            self.rec_dot.set_visible(True)
            self.status_row.set_subtitle(_("Recording"))
        elif kind is EventKind.SAVED:
            self._add_saved(event)
            self._idle()
        elif kind in (EventKind.DROPPED, EventKind.ERROR):
            self._idle()
        elif kind is EventKind.READY:
            self.rec_row.set_subtitle(_("(in progress, ignored) {track}").format(track=_markup_safe(event.track.label)) if event.track else "—")
            self._idle()
        elif kind is EventKind.STOPPED:
            self.rec_dot.set_visible(False)
            self.status_row.set_subtitle(_("Stopped"))
            if self.session and self.session.finished_by_limit:
                GLib.idle_add(self._switch_off_after_limit)  # not from inside the session's own callback

    def _switch_off_after_limit(self) -> bool:
        if self.session and self.session.finished_by_limit:
            self.switch.set_active(False)  # runs the normal stop path (clears the session, unlocks the settings)
        return GLib.SOURCE_REMOVE

    def _idle(self) -> None:
        self.rec_dot.set_visible(False)
        self.status_row.set_subtitle(_("Waiting for the next track…"))

    def _add_saved(self, event: Event) -> None:
        row = Adw.ActionRow(title=_markup_safe(event.track.label),
                            subtitle=_markup_safe(event.path.name))
        play = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text=_("Open in default player"))
        play.connect("clicked", lambda *_: Gio.AppInfo.launch_default_for_uri(event.path.as_uri(), None))
        row.add_suffix(play)
        self.saved_group.add(row)
        self.saved_group.set_visible(True)

    # ---------- UI ----------
    def _on_pick_dir(self, _btn) -> None:
        dialog = Gtk.FileDialog(title=_("Output folder"))
        dialog.select_folder(self, None, self._on_dir_chosen)

    def _on_dir_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        self.out_dir = Path(folder.get_path())
        self._show_out_dir()

    def _log(self, text: str) -> None:
        if not hasattr(self, "log_view"):  # called during construction
            print(text)
            return
        # A stack: the newest line goes on top; the oldest ones fall off the bottom.
        self.log_buf.insert(self.log_buf.get_start_iter(), text + "\n")
        self.log_buf.place_cursor(self.log_buf.get_start_iter())  # keep the cursor (which GTK scrolls to) up here
        extra = self.log_buf.get_line_count() - 1 - LOG_MAX_LINES  # the last "line" is the empty one after the final \n
        if extra > 0:  # keep the log bounded in a long session
            end = self.log_buf.get_end_iter()
            self.log_buf.delete(self.log_buf.get_iter_at_line(LOG_MAX_LINES)[1], end)
        # staying on the newest line is done by _follow_log() once the new height is known

    def _on_log_scrolled(self, adjustment) -> None:
        """Reaching the top (by the user or by us) resumes following; moving elsewhere never stops it."""
        if adjustment.get_value() <= 1:
            self._log_follow = True

    def _on_log_wheel(self, _controller, _dx, dy) -> bool:
        if dy > 0:  # scrolling down: the user wants to read older lines
            self._stop_following_log()
        return False  # let the scroll through

    def _stop_following_log(self) -> None:
        self._log_follow = False

    def _follow_log(self, adjustment, _pspec=None) -> None:
        """New content changed the height (this runs after layout, unlike scrolling right after insert)."""
        if self._log_follow:
            adjustment.set_value(0)

    def _show_out_dir(self) -> None:
        """Label only: "~/Music/tapwire" instead of the full home path. self.out_dir itself is untouched."""
        self.dir_row.set_subtitle(_markup_safe(display_path(self.out_dir)))
        self.dir_row.set_tooltip_text(str(self.out_dir))  # the full path is one hover away

    def _on_log_changed(self, buffer) -> None:
        has_text = buffer.get_char_count() > 0
        for button in (getattr(self, "copy_btn", None), getattr(self, "clear_btn", None)):
            if button:
                button.set_sensitive(has_text)

    def _on_copy_log(self, button) -> None:
        """Copies the log exactly as shown (newest line first) and confirms on the button itself."""
        text = self.log_buf.get_text(self.log_buf.get_start_iter(), self.log_buf.get_end_iter(), False)
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
        button.set_label(_("Copied!"))
        GLib.timeout_add(1200, lambda: (button.set_label(_("Copy logs")), False)[1])

    def _on_clear_log(self, _button) -> None:
        self.log_buf.set_text("")
        self._log_follow = True  # start following the newest line again

    def _on_close(self, *_) -> bool:
        if self.session:
            self.session.stop()
        return False
