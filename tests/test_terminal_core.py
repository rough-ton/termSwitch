import pyte

from termswitch.terminal_core import (TerminalScreen, encode_key, paste_line_count,
                                      resolve_color, sanitize_paste)


def make(cols=20, rows=5, **kw):
    s = TerminalScreen(cols, rows, history=100, **kw)
    return s, pyte.Stream(s)


def text(s):
    return [s.line_text(s.buffer[y]) for y in range(s.lines)]


def test_scrollback_collects_lines():
    s, st = make()
    st.feed("".join(f"l{i}\r\n" for i in range(8)))
    assert [s.line_text(x) for x in s.scrollback] == ["l0", "l1", "l2", "l3"]
    assert text(s)[:4] == ["l4", "l5", "l6", "l7"]


def test_alt_screen_restores_main_and_skips_history():
    s, st = make()
    st.feed("main\r\n")
    st.feed("\x1b[?1049h")
    assert s.alt_active
    st.feed("".join(f"alt{i}\r\n" for i in range(10)))
    assert not s.scrollback        # alt screen output never reaches scrollback
    st.feed("\x1b[?1049l")
    assert not s.alt_active
    assert text(s)[0] == "main"


def test_shrink_keeps_cursor_and_moves_lines_to_scrollback():
    s, st = make(rows=5)
    st.feed("a\r\nb\r\nc\r\nd\r\ne")
    s.resize(3, 20)
    assert text(s) == ["c", "d", "e"]
    assert [s.line_text(x) for x in s.scrollback] == ["a", "b"]
    s2, st2 = make(rows=5)
    st2.feed("only")
    s2.resize(3, 20)
    assert text(s2)[0] == "only" and not s2.scrollback


def test_tab_stops_extend_when_wider():
    s, st = make(cols=80)
    s.resize(5, 121)
    assert {80, 88, 96, 104, 112, 120} <= s.tabstops


def test_device_status_reply_and_title():
    replies, titles = [], []
    s, st = make(reply=replies.append, on_title=titles.append)
    st.feed("\x1b[6n\x1b]0;hello\x07")
    assert replies and replies[0].startswith("\x1b[")
    assert titles == ["hello"]


def test_clipboard_and_hyperlink_osc_are_ignored():
    replies = []
    s, st = make(reply=replies.append)
    st.feed("\x1b]52;c;ZXZpbA==\x07\x1b]8;;https://evil.example\x07x\x1b]8;;\x07")
    assert text(s)[0] == "x"
    assert not replies


def test_clear_scrollback():
    s, st = make()
    st.feed("".join(f"l{i}\r\n" for i in range(8)))
    st.feed("\x1b[H\x1b[2J\x1b[3J")
    assert not s.scrollback


def test_modes():
    s, st = make()
    st.feed("\x1b[?1h\x1b[?2004h")
    assert s.app_cursor_keys and s.bracketed_paste
    st.feed("\x1b[?1l\x1b[?2004l")
    assert not s.app_cursor_keys and not s.bracketed_paste


def test_encode_keys():
    assert encode_key("enter") == "\r"
    assert encode_key("backspace") == "\x7f"
    assert encode_key("c", "c", ctrl=True) == "\x03"
    assert encode_key("up") == "\x1b[A"
    assert encode_key("up", app_cursor=True) == "\x1bOA"
    assert encode_key("left", ctrl=True) == "\x1b[1;5D"
    assert encode_key("f1") == "\x1bOP"
    assert encode_key("f5") == "\x1b[15~"
    assert encode_key("delete") == "\x1b[3~"
    assert encode_key("pageup", shift=True) == "\x1b[5;2~"
    assert encode_key("tab", shift=True) == "\x1b[Z"
    assert encode_key("x", "x", alt=True) == "\x1bx"
    assert encode_key("[", "", ctrl=True) == "\x1b"
    assert encode_key("a", "a") == "a"


def test_paste_sanitizing():
    evil = "ls\n\x1b[201~rm -rf ~\x07\r\n"
    out = sanitize_paste(evil, bracketed=True)
    assert out.startswith("\x1b[200~") and out.endswith("\x1b[201~")
    inner = out[len("\x1b[200~"):-len("\x1b[201~")]
    assert "\x1b" not in inner and "\x07" not in inner
    assert inner == "ls\r[201~rm -rf ~\r"
    assert sanitize_paste("a\tb", bracketed=False) == "a\tb"
    assert paste_line_count("one") == 1
    assert paste_line_count("one\n") == 1
    assert paste_line_count("one\ntwo\r\nthree") == 3


def test_colors():
    assert resolve_color("default", "#000000") == "#000000"
    assert resolve_color("red", "#000000").startswith("#")
    assert resolve_color("ff8800", "#000000") == "#ff8800"
    assert resolve_color("bogus", "#123456") == "#123456"
