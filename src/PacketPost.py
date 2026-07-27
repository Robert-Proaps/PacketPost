import asyncio                     # FIX: was missing entirely
import threading
import queue
import tkinter as tk
from tkinter import ttk
from meshcore import MeshCore

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
    def __init__(self, incoming_q: queue.Queue):
        self.incoming_q = incoming_q
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
        self.loop.run_until_complete(self._async_main())
        self.loop.run_forever()  # Keeps the thread's loop alive so that later commands
                                   # from the GUI thread have somewhere to go.

    async def _async_main(self):
        """
        RADIO CONNECT + LISTEN
        This is where meshcore_py setup happens: open the serial connection,
        subscribe to incoming packet events, etc.
        """
        # self.mc = await MeshCore.create_serial("/dev/ttyUSB0")
        #
        # def on_message(packet):
        #     self.incoming_q.put(packet)
        #
        # self.mc.subscribe(on_message)   # exact API depends on meshcore_py

        pass  # replace with real connect/subscribe logic.

    async def send_command(self, text: str):
        """
        OUTGOING COMMAND
        A coroutine that actually talks to the radio. This only ever gets
        *called* via run_coroutine_threadsafe from the GUI thread --
        never called directly from Tkinter code.
        """
        # await self.mc.send_message(text)
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
