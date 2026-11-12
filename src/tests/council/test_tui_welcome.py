import asyncio

from typer.testing import CliRunner

from aletheore.council import cli
from aletheore.council.tui_welcome import SPONSOR, Actions, WelcomeApp, looks_like_link


class Fake:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def actions(self):
        def check(server):
            self.calls.append(("check", server))
            return (True, 42, "the server answered") if self.ok else (False, 0, "could not reach it")

        def register(server, code, handle):
            self.calls.append(("register", server, code, handle))
            return f"registered as {handle}"

        def join(link, handle):
            self.calls.append(("join", link, handle))
            return "asked to join; tell the host the words"

        return Actions(check, register, join)


async def settle(pilot, n=6):
    for _ in range(n):
        await pilot.pause(0.1)


def test_link_detection():
    assert looks_like_link("https://x.example/j/abc#secret.dev") and not looks_like_link("ABCD-1234") and not looks_like_link("https://x/")


def test_nothing_is_sent_before_enter_and_reachability_reports_latency(tmp_path):
    fake = Fake()

    async def go():
        app = WelcomeApp("dev_abc", "calm-otter-reef-lamp", fake.actions(), tmp_path / "shown")
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            assert "dev_abc" in str(app.query_one("#device").render()) and "CALM OTTER REEF LAMP" in str(app.query_one("#device").render())
            await pilot.press(*"https://room.example")
            await settle(pilot, 3)
            assert fake.calls == []  # typing sends nothing
            await pilot.press("enter")
            await settle(pilot, 6)
            assert fake.calls == [("check", "https://room.example")]
            assert "42 ms" in str(app.query_one("#reach").render())

    asyncio.run(go())


def test_register_with_a_code_and_join_with_a_link(tmp_path):
    fake = Fake()

    async def go(credential, expect):
        app = WelcomeApp("dev_abc", "a-b-c-d", fake.actions(), tmp_path / "shown", server="https://room.example")
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("#credential").value = credential
            app.query_one("#handle").value = "dana"
            app.query_one("#handle").focus()
            await pilot.press("enter")
            await settle(pilot, 6)
            assert fake.calls[-1][0] == expect
            assert "dana" in str(app.query_one("#result").render()) or "asked to join" in str(app.query_one("#result").render())

    asyncio.run(go("REG-CODE-1", "register"))
    asyncio.run(go("https://room.example/j/abc#secret.dev", "join"))


def test_missing_fields_send_nothing(tmp_path):
    fake = Fake()

    async def go():
        app = WelcomeApp("dev_abc", "a-b-c-d", fake.actions(), tmp_path / "shown")
        async with app.run_test(size=(120, 40)) as pilot:
            await settle(pilot)
            app.query_one("#handle").focus()
            await pilot.press("enter")
            await settle(pilot, 3)
            assert fake.calls == [] and "fill in" in str(app.query_one("#result").render())

    asyncio.run(go())


def test_sponsor_line_is_shown_once(tmp_path):
    marker = tmp_path / "shown"

    async def show():
        app = WelcomeApp("d", "a-b-c-d", Fake().actions(), marker)
        async with app.run_test(size=(140, 40)) as pilot:
            await settle(pilot)
            return str(app.query_one("#sponsor").render())

    first, second = asyncio.run(show()), asyncio.run(show())
    assert SPONSOR in first and "sponsor" not in second and "—" not in SPONSOR and "–" not in SPONSOR


def test_no_arguments_without_a_terminal_still_shows_the_commands():
    out = CliRunner().invoke(cli.app, [])
    assert out.exit_code == 0 and "Commands" in out.output and "serve" in out.output
