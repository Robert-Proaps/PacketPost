import asyncio                     # FIX: was missing entirely
import threading
import queue
import tkinter as tk
from tkinter import ttk
from meshcore import MeshCore, EventType

# This program uses at least 2 threads at any given time, this is so that non-blocking
# communication to the radio can occur while Tkinter is also able to draw the display
# and UI. There are only two interaction points between the two threads which will be
# referred to as "mailboxes"

# The Mailboxes
incoming_q = queue.Queue()   # Radio --> GUI
# outgoing_q removed: commands go GUI -> Radio via run_coroutine_threadsafe instead,
# so a second queue isn't needed for that direction.


# The Background Thread - all of this is for handling communication with the radio.
# ===============================================================================================================
class RadioWorker:
    def __init__(self, incoming_q: queue.Queue, port: str = "/dev/ttyUSB0"):
        self.incoming_q = incoming_q
        self.port = port
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mc = None  # will hold the meshcore Client instance
        self.connected = False  # GUI checks this before trying to send

    def start_in_thread(self):
        """Called once from the main thread at program startup"""
        t = threading.Thread(target=self._thread_main, daemon=True)
        t.start()

    def _thread_main(self):
        """Runs entirely in the background thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._async_main(self.port))
            self.loop.run_forever()  # Keeps the thread's loop alive so that later
                                       # commands from the GUI thread have somewhere
                                       # to go.
        except Exception as exc:
            # Anything that escapes _async_main (or a crash inside run_forever)
            # used to just kill this thread silently, leaving self.loop pointing
            # at a dead loop with no explanation in the GUI. Now it's reported
            # through the same mailbox as everything else.
            self.connected = False
            self.incoming_q.put(f"[ERROR] radio thread crashed: {exc}")
        finally:
            # Make sure on_send stops trying to schedule work on a loop that
            # is no longer running.
            self.connected = False

    async def _async_main(self, port: str = "/dev/ttyUSB0"):
        """
        RADIO CONNECT + LISTEN
        Opens the serial connection, subscribes to incoming message events,
        and starts auto-fetching so the library pushes new messages to us
        instead of us having to poll for them.
        """
        try:
            self.mc = await MeshCore.create_serial(port, 115200)
        except Exception as exc:
            # Connection failed (wrong port, device unplugged, permissions, etc.)
            # Report it through the SAME mailbox the GUI already polls, so the
            # error shows up in the Text widget instead of crashing silently
            # in the background thread.
            self.incoming_q.put(f"[ERROR] could not open {port}: {exc}")
            return

        try:
            # Handler runs inside the asyncio thread -- its ONLY job is to hand
            # the event payload to the queue. Never touch Tkinter widgets here.
            async def on_message(event):
                data = event.payload
                text = data.get("text", "")
                sender = data.get("pubkey_prefix", "unknown")
                self.incoming_q.put(f"{sender}: {text}")

            self.mc.subscribe(EventType.CONTACT_MSG_RECV, on_message)

            # Without this, incoming messages sit on the device until something
            # explicitly calls get_msg() -- auto-fetching makes them arrive as
            # CONTACT_MSG_RECV events on their own.
            await self.mc.start_auto_message_fetching()
        except Exception as exc:
            # Connected fine, but setup after that failed (bad API call, device
            # hiccup, etc). Previously this would propagate up, kill the thread,
            # and leave the GUI thinking everything was fine.
            self.incoming_q.put(f"[ERROR] setup failed after connecting: {exc}")
            return

        self.connected = True
        self.incoming_q.put(f"[connected on {port}]")

    async def execute_command(self, cmd_name: str, *args):
        """
        CORE COMMAND DISPATCHER
        This is the ONE place that actually talks to self.mc.commands. Every
        way of triggering a command -- the textbox parser, a macro button,
        anything added later -- funnels through here. That means error
        handling and reporting-to-the-GUI only has to exist in one spot.

        cmd_name must match a method name on self.mc.commands (e.g.
        "get_contacts", "reboot", "send_msg"). args are passed through
        positionally, already the right type -- this function does no
        string parsing or casting itself.
        """
        if self.mc is None or not self.connected:
            self.incoming_q.put("[ERROR] not connected, cannot send")
            return

        method = getattr(self.mc.commands, cmd_name, None)
        if method is None:
            self.incoming_q.put(f"[ERROR] unknown command: {cmd_name}")
            return

        try:
            result = await method(*args)
            self.incoming_q.put(f"[{cmd_name}] {result}")
        except Exception as exc:
            # Anything the command call raises (serial dropped, bad args,
            # device rejected it, etc.) is reported through the mailbox
            # instead of disappearing into this background thread.
            self.incoming_q.put(f"[ERROR] command '{cmd_name}' failed: {exc}")

    async def parse_and_send_text(self, raw_text: str):
        """
        TEXTBOX ENTRY POINT
        Parses free-typed text ("reboot", "set_name My Node") into a
        cmd_name + args and hands it to execute_command. This is the only
        place that does string splitting -- macro buttons skip this
        entirely and call execute_command directly with args already in
        the right shape.
        """
        parts = raw_text.strip().split()
        if not parts:
            return
        cmd_name, *args = parts
        await self.execute_command(cmd_name, *args)


# GUI Thread (Tkinter Side)
# ===================================================================================================
class App(tk.Tk):
    def __init__(self, radio: RadioWorker):
        super().__init__()
        self.radio = radio
        self.title("PacketPost")

        self.geometry("720x480")     # FIX: was self.geometer(...)
        self.minsize(600, 400)       # FIX: was a single string "600x400"

        # setup grid (3x3)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)

        # widgets
        # FIX: parent was `root` (undefined) -> now `self`
        # FIX: added command= so the button actually calls on_send
        button = ttk.Button(self, text="Send", command=self.on_send)
        button.grid(column=0, row=1)

        # FIX: everything below used .pack(), which conflicts with the .grid()
        # calls above in the same container (self). Converted to .grid() so the
        # whole window uses one consistent geometry manager.
        self.output = tk.Text(self, height=15, width=50)
        self.output.grid(column=0, row=0, columnspan=3, sticky="nsew")

        self.entry = tk.Entry(self, width=40)
        self.entry.grid(column=1, row=1, sticky="ew")

        # Macro buttons -- row 2 was already reserved in the grid but unused.
        # Each of these is one line: label, the command name on
        # self.mc.commands, and any fixed args it needs. Add more here as
        # you find commands worth turning into a button.
        self.make_macro_button(self, "Reboot", "reboot", row=2, column=0)
        self.make_macro_button(self, "Get Contacts", "get_contacts", row=2, column=1)

        # Start polling the incoming queue.
        # This is how data coming from the radio reaches the gui.
        # after() re-schedules itself, acting like a lightweight timer loop which
        # lives inside Tkinter's own mainloop.
        self.after(100, self.poll_incoming)

    def poll_incoming(self):
        """Runs in the MAIN thread. Safe to touch widgets here"""
        try:
            while True:
                packet = incoming_q.get_nowait()
                self.output.insert(tk.END, f"RX: {packet}\n")
                self.output.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self.poll_incoming)  # Reschedule

    def run_on_radio(self, coro):
        """
        SHARED DISPATCH HELPER (GUI THREAD)
        Every trigger for radio work -- the Send button, a macro button,
        anything added later -- should go through this instead of calling
        run_coroutine_threadsafe directly. It's the one place that checks
        the connection is alive and reports a clear error if it isn't.

        coro: an already-created coroutine, e.g. self.radio.execute_command(...)
        """
        if self.radio.loop is None or not self.radio.connected:
            self.output.insert(tk.END, "[ERROR] not connected, cannot send\n")
            self.output.see(tk.END)
            return
        asyncio.run_coroutine_threadsafe(coro, self.radio.loop)

    def on_send(self):
        text = self.entry.get()
        if not text:
            return
        self.run_on_radio(self.radio.parse_and_send_text(text))
        self.entry.delete(0, tk.END)

    def make_macro_button(self, parent, label: str, cmd_name: str, *args, **grid_kwargs):
        """
        MACRO BUTTON FACTORY
        Creates a button that fires a fixed command with fixed args --
        no textbox parsing involved. To add a new macro later, add one
        call like:

            self.make_macro_button(self, "Reboot", "reboot", row=2, column=0)
            self.make_macro_button(self, "Get Contacts", "get_contacts", row=2, column=1)

        Any extra keyword args (row=, column=, sticky=, etc.) are passed
        straight to .grid() on the resulting button, so placement stays
        the caller's choice.
        """
        button = ttk.Button(
            parent,
            text=label,
            command=lambda: self.run_on_radio(self.radio.execute_command(cmd_name, *args)),
        )
        button.grid(**grid_kwargs)
        return button


# =====================================================================================
#   PROGRAM ENTRY POINT
# =====================================================================================
if __name__ == "__main__":
    radio = RadioWorker(incoming_q)
    radio.start_in_thread()

    app = App(radio)
    app.mainloop()
