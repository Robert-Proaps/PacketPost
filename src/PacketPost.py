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

    def start_in_thread(self):
        """Called once from the main thread at program startup"""
        t = threading.Thread(target=self._thread_main, daemon=True)
        t.start()

    def _thread_main(self):
        """Runs entirely in the background thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._async_main(self.port))
        self.loop.run_forever()  # Keeps the thread's loop alive so that later commands
                                   # from the GUI thread have somewhere to go.

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

        self.incoming_q.put(f"[connected on {port}]")

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

    async def send_command(self, text: str):
        """
        OUTGOING COMMAND
        A coroutine that actually talks to the radio. This only ever gets
        *called* via run_coroutine_threadsafe from the GUI thread --
        never called directly from Tkinter code.

        NOTE: send_msg() needs a destination contact/key -- sending requires
        picking a contact first. This stub just demonstrates the call shape;
        wire up contact selection (e.g. a dropdown fed by get_contacts())
        before this will actually work.
        """
        if self.mc is None:
            self.incoming_q.put("[ERROR] not connected, cannot send")
            return

        # Example once you have a destination contact/key:
        # result = await self.mc.commands.send_msg(dst, text)
        # if result.type == EventType.ERROR:
        #     self.incoming_q.put(f"[ERROR] send failed: {result.payload}")
        # else:
        #     self.incoming_q.put(f"[sent] {text}")
        print(f"[radio thread] would send: {text}")


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

    def on_send(self):
        text = self.entry.get()
        if not text or self.radio.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.radio.send_command(text),
            self.radio.loop,
        )
        self.entry.delete(0, tk.END)


# =====================================================================================
#   PROGRAM ENTRY POINT
# =====================================================================================
if __name__ == "__main__":
    radio = RadioWorker(incoming_q)
    radio.start_in_thread()

    app = App(radio)
    app.mainloop()
