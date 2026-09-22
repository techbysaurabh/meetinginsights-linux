#!/usr/bin/env python3
"""MeetingInsights — GTK desktop app for on-device meeting recording, transcription and notes.

The UI is a thin layer over the `meetinginsights` CLI: it shells out for start/stop and
reads the resulting markdown from the notes directory. Everything — recording,
transcription and note writing — happens on this machine; nothing is uploaded.
"""
import html
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.0")
gi.require_version("Notify", "0.7")
from gi.repository import Gdk, GLib, Gtk, Notify, Pango, WebKit2  # noqa: E402

HOME = Path.home()
NOTES_DIR = Path(os.environ.get("MEETINGINSIGHTS_OUT", HOME / "MeetingNotes"))
REC_DIR = Path(os.environ.get("MEETINGINSIGHTS_HOME", HOME / ".local/share/meetinginsights")) / "rec"
CLI = os.environ.get("MEETINGINSIGHTS_CLI") or shutil.which("meetinginsights") or str(
    Path(__file__).resolve().parent.parent / "bin" / "meetinginsights")

# ---------------------------------------------------------------- theme
# Every colour, weight and radius lives here — the one-file design system the
# iOS playbook prescribes, so a rebrand is a single edit rather than a sweep
# through every view. Palette placeholders below; swap for the MeetingInsights
# values to match the iPhone app exactly.
THEME = {
    "bg":            "#07090E",   # sampled from the app icon background
    "bg_glow":       "#131A29",   # the icon's lit upper corner
    "surface":       "#0D1219",
    "hairline":      "rgba(255,255,255,0.08)",
    "accent":        "#66B29D",   # the icon's mint bubble
    "accent_bright": "#93DFC7",   # the icon's sparkle — AI indicator only
    "text_primary":  "#ECF2EF",
    "text_secondary":"#7C8B93",
    "danger":        "#C0563F",
}

CSS = ("""
window, .root {
  background-image: radial-gradient(circle farthest-corner at 50%% 0%%, %(bg_glow)s 0%%, %(bg)s 60%%);
  background-color: %(bg)s;
}
.sidebar { background: transparent; border-right: 1px solid %(hairline)s; }

/* dramatic scale contrast: a thin hero numeral against tracked micro-labels */
.timer {
  font-family: monospace; font-size: 34px; font-weight: 200;
  color: %(text_primary)s;
}
.micro {
  font-size: 10px; font-weight: 600; letter-spacing: 1.4px;
  color: %(text_secondary)s;
}
.status-rec  { color: %(danger)s; }
.status-busy { color: %(accent)s; }

entry {
  background: %(surface)s; color: %(text_primary)s;
  border: 1px solid %(hairline)s; border-radius: 8px; padding: 9px 12px;
  font-size: 14px;
}
entry:focus { border-color: %(accent)s; }

/* one loud button, one quiet one — the whole accent-rationing rule */
button.loud {
  background: %(accent)s; color: #10131A; border: none;
  border-radius: 8px; padding: 10px 22px; font-weight: 700;
}
button.loud:hover { background: shade(%(accent)s, 1.08); }
button.quiet {
  background: %(surface)s; color: %(text_primary)s;
  border: 1px solid %(hairline)s; border-radius: 8px;
  padding: 10px 22px; font-weight: 600;
}
button.quiet:hover { background: shade(%(surface)s, 1.3); }
button.loud:disabled, button.quiet:disabled {
  background: %(surface)s; color: %(text_secondary)s; border: 1px solid %(hairline)s;
}

list, list row { background: transparent; }
list row { border-bottom: 1px solid %(hairline)s; padding: 12px 14px; }
list row:selected { background: alpha(%(accent)s, 0.10); }
.row-title { color: %(text_primary)s; font-weight: 600; }
.row-meta  { color: %(text_secondary)s; font-size: 11px; letter-spacing: 0.5px; }
.empty     { color: %(text_secondary)s; font-size: 13px; }
""" % THEME).encode()

NOTE_CSS = """
:root { color-scheme: dark; }
body { background:#1c1f26; color:#d6d9e0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       font-size: 14px; line-height: 1.65; padding: 26px 30px; margin:0; }
h1 { font-size: 21px; color:#fff; margin:0 0 4px; }
h2 { font-size: 15px; color:#e6e6e6; margin: 26px 0 8px; padding-bottom:6px;
     border-bottom:1px solid #2a2e37; }
h3 { font-size: 13.5px; color:#cfd3dc; margin: 16px 0 4px; }
table { border-collapse: collapse; width:100%; margin:10px 0; font-size:13px; }
th { text-align:left; background:#262a33; color:#b8bccb; padding:7px 10px; border:1px solid #2a2e37; }
td { padding:7px 10px; border:1px solid #2a2e33; vertical-align: top; }
code { background:#262a33; padding:1px 5px; border-radius:4px; font-size:12.5px; }
blockquote { border-left:3px solid #3b6fe0; margin:12px 0; padding:2px 14px; color:#b8bccb; }
strong { color:#fff; }
ul,ol { padding-left: 22px; }
li { margin: 3px 0; }
a { color:#7aa2ff; }
"""


def md_to_html(md: str) -> str:
    """Minimal markdown → HTML. Avoids a hard dependency on the markdown package."""
    try:
        import markdown as _md
        body = _md.markdown(md, extensions=["tables", "fenced_code"])
        return f"<style>{NOTE_CSS}</style>{body}"
    except ImportError:
        pass

    out, in_table, in_list = [], False, False
    for line in md.splitlines():
        s = line.rstrip()
        if re.match(r"^\s*\|.*\|\s*$", s):
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            if re.match(r"^[\s|:-]+$", s):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                out.append("<table>"); in_table = True
            out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>"); in_table = False
        if m := re.match(r"^(#{1,4})\s+(.*)", s):
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<h{len(m.group(1))}>{inline(m.group(2))}</h{len(m.group(1))}>")
        elif m := re.match(r"^\s*[-*]\s+(.*)", s):
            if not in_list: out.append("<ul>"); in_list = True
            out.append(f"<li>{inline(m.group(1))}</li>")
        elif not s.strip():
            if in_list: out.append("</ul>"); in_list = False
        else:
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<p>{inline(s)}</p>")
    if in_list: out.append("</ul>")
    if in_table: out.append("</table>")
    return f"<style>{NOTE_CSS}</style>" + "\n".join(out)


def inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return t


class MeetingInsightsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MeetingInsights")
        self.set_default_size(1120, 720)
        self.busy = False
        self.started_at = None

        hb = Gtk.HeaderBar(show_close_button=True, title="MeetingInsights")
        hb.set_subtitle("records, transcribes and writes notes — entirely on your device")
        self.set_titlebar(hb)
        self.refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_btn.set_tooltip_text("Reload meetings")
        self.refresh_btn.connect("clicked", lambda *_: self.load_meetings())
        hb.pack_end(self.refresh_btn)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.get_style_context().add_class("root")
        self.add(root)
        root.pack_start(self._build_controls(), False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(330)
        root.pack_start(paned, True, True, 0)

        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.get_style_context().add_class("sidebar")
        self.listbox = Gtk.ListBox()
        self.listbox.connect("row-selected", self.on_select)
        sw.add(self.listbox)
        paned.pack1(sw, False, False)

        self.webview = WebKit2.WebView()
        self.webview.set_background_color(Gdk.RGBA(0.11, 0.12, 0.15, 1))
        paned.pack2(self.webview, True, False)

        self.load_meetings()
        self.poll_state()
        GLib.timeout_add_seconds(1, self.tick)

    def _build_controls(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(14); box.set_margin_bottom(14)
        box.set_margin_start(16); box.set_margin_end(16)

        self.entry = Gtk.Entry(placeholder_text="Meeting title  (e.g. SSO standup)")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda *_: self.on_record())
        box.pack_start(self.entry, True, True, 0)

        self.timer_lbl = Gtk.Label(label="00:00")
        self.timer_lbl.get_style_context().add_class("timer")
        box.pack_start(self.timer_lbl, False, False, 0)

        self.status_lbl = Gtk.Label(label="Idle")
        self.status_lbl.get_style_context().add_class("micro")
        box.pack_start(self.status_lbl, False, False, 0)

        self.rec_btn = Gtk.Button(label="Record")
        self.rec_btn.get_style_context().add_class("loud")
        self.rec_btn.connect("clicked", lambda *_: self.on_record())
        box.pack_start(self.rec_btn, False, False, 0)
        return box

    # ---------- state ----------
    def cli(self, *args):
        return subprocess.run([CLI, *args], capture_output=True, text=True, timeout=900)

    def poll_state(self):
        r = self.cli("status")
        recording = "Recording" in r.stdout
        self.set_mode("recording" if recording else "idle")
        if recording:
            m = re.search(r"for (\d+)m", r.stdout)
            self.started_at = time.time() - (int(m.group(1)) * 60 if m else 0)
            t = re.search(r'Recording "(.+?)"', r.stdout)
            if t:
                self.entry.set_text(t.group(1))

    def set_mode(self, mode, note=""):
        ctx = self.status_lbl.get_style_context()
        for c in ("status-rec", "status-busy"):
            ctx.remove_class(c)
        btn = self.rec_btn.get_style_context()
        for c in ("loud", "quiet"):
            btn.remove_class(c)
        self.mode = mode
        if mode == "recording":
            self.status_lbl.set_text("● RECORDING"); ctx.add_class("status-rec")
            self.rec_btn.set_label("Stop"); btn.add_class("quiet")
            self.rec_btn.set_sensitive(True); self.entry.set_sensitive(False)
        elif mode == "busy":
            self.status_lbl.set_text(note or "Working…"); ctx.add_class("status-busy")
            self.rec_btn.set_label("Working…"); btn.add_class("quiet")
            self.rec_btn.set_sensitive(False); self.entry.set_sensitive(False)
        else:
            self.status_lbl.set_text("IDLE")
            self.rec_btn.set_label("Record"); btn.add_class("loud")
            self.rec_btn.set_sensitive(True); self.entry.set_sensitive(True)
            self.timer_lbl.set_text("00:00"); self.started_at = None

    def tick(self):
        # reconcile with the CLI every 5s so the UI stays correct when the
        # recording is started or stopped from a terminal (or dies on its own)
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self.mode != "busy" and self._ticks % 5 == 0:
            try:
                live = "Recording" in self.cli("status").stdout
            except Exception:
                live = self.mode == "recording"
            if live and self.mode == "idle":
                self.poll_state()
            elif not live and self.mode == "recording":
                self.set_mode("idle")
                self.load_meetings()
        if self.mode == "recording" and self.started_at:
            e = int(time.time() - self.started_at)
            self.timer_lbl.set_text(f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}"
                                    if e >= 3600 else f"{e//60:02d}:{e%60:02d}")
        return True

    # ---------- actions ----------
    def on_record(self):
        if self.mode == "recording":
            self.set_mode("busy", "Transcribing…")
            threading.Thread(target=self._stop_worker, daemon=True).start()
        elif self.mode == "idle":
            title = self.entry.get_text().strip() or "meeting"
            r = self.cli("start", title)
            if r.returncode == 0:
                self.started_at = time.time()
                self.set_mode("recording")
                self.notify("Recording started", title)
            else:
                self.error_dialog("Could not start recording", r.stderr or r.stdout)

    def _stop_worker(self):
        r = self.cli("stop")
        GLib.idle_add(self._stop_done, r)

    def _stop_done(self, r):
        self.set_mode("idle")
        self.load_meetings()
        if r.returncode == 0:
            self.notify("Notes ready", "Meeting notes have been written.")
            if self.listbox.get_row_at_index(0):
                self.listbox.select_row(self.listbox.get_row_at_index(0))
        else:
            self.error_dialog("Recording processed with errors", (r.stderr or r.stdout)[-800:])
        return False

    # ---------- meetings ----------
    def load_meetings(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        files = sorted(NOTES_DIR.glob("*.md"), reverse=True) if NOTES_DIR.exists() else []
        if not files:
            row = Gtk.ListBoxRow(selectable=False)
            lbl = Gtk.Label(label="No meetings yet.\nEnter a title and press Record.")
            lbl.get_style_context().add_class("empty")
            lbl.set_justify(Gtk.Justification.CENTER)
            lbl.set_margin_top(40)
            row.add(lbl); self.listbox.add(row); self.listbox.show_all()
            self.webview.load_html(f"<style>{NOTE_CSS}</style>"
                                   "<p style='color:#6b7080'>Select a meeting to read its notes.</p>", None)
            return
        for f in files:
            self.listbox.add(self._meeting_row(f))
        self.listbox.show_all()

    def _meeting_row(self, path: Path):
        stem = path.stem
        m = re.match(r"(\d{4}-\d{2}-\d{2})[_-](\d{2})-(\d{2})_(.*)", stem)
        if m:
            date, hh, mm, slug = m.groups()
            title = slug.replace("-", " ").title()
            meta = f"{date}  ·  {hh}:{mm}"
        elif (m2 := re.match(r"(\d{4}-\d{2}-\d{2})_(.*)", stem)):
            title = m2.group(2).replace("-", " ").replace("_", " ").title()
            meta = m2.group(1)
        else:
            title, meta = stem.replace("-", " ").title(), ""
        row = Gtk.ListBoxRow()
        row.path = path
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("row-title")
        t.set_ellipsize(Pango.EllipsizeMode.END)
        d = Gtk.Label(label=meta, xalign=0)
        d.get_style_context().add_class("row-meta")
        box.pack_start(t, False, False, 0); box.pack_start(d, False, False, 0)
        row.add(box)
        return row

    def on_select(self, _lb, row):
        if not row or not hasattr(row, "path"):
            return
        try:
            md = row.path.read_text()
        except OSError as e:
            md = f"# Could not read notes\n\n{e}"
        transcript = REC_DIR / (row.path.stem + ".txt")
        html_doc = md_to_html(md)
        if transcript.exists():
            t = html.escape(transcript.read_text())
            html_doc += ("<h2>Transcript</h2><details><summary style='cursor:pointer;color:#7aa2ff'>"
                         "Show full transcript</summary>"
                         f"<pre style='white-space:pre-wrap;font-size:12.5px;color:#b8bccb'>{t}</pre></details>")
        self.webview.load_html(html_doc, None)

    # ---------- helpers ----------
    def notify(self, summary, body):
        try:
            n = Notify.Notification.new(summary, body, "meetinginsights")
            n.show()
        except Exception:
            pass

    def error_dialog(self, primary, detail):
        d = Gtk.MessageDialog(transient_for=self, modal=True,
                              message_type=Gtk.MessageType.ERROR,
                              buttons=Gtk.ButtonsType.CLOSE, text=primary)
        d.format_secondary_text((detail or "").strip()[:600])
        d.run(); d.destroy()


class MeetingInsightsApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="page.saurabh.MeetingInsights")

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Notify.init("MeetingInsights")
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.get_active_window() or MeetingInsightsWindow(self)
        win.show_all()
        win.present()


if __name__ == "__main__":
    import sys
    sys.exit(MeetingInsightsApp().run(sys.argv))
