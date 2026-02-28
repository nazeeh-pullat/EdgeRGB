import time
import threading
import numpy as np
import serial
from mss import mss
from serial.tools import list_ports

import customtkinter as ctk
from tkinter import messagebox
from PIL import Image, ImageDraw, ImageTk



APP_NAME = "EdgeRGB"
APP_VERSION = "1.08"


# -----------------------
# Helpers
# -----------------------
def list_com_ports():
    return [p.device for p in list_ports.comports()]


def list_monitors():
    with mss() as sct:
        mons = []
        for i in range(1, len(sct.monitors)):  # skip 0 = all monitors combined
            m = sct.monitors[i]
            mons.append((i, f"Monitor {i}  {m['width']}x{m['height']}  ({m['left']},{m['top']})"))
        return mons


def clamp255(x: int) -> int:
    return 0 if x < 0 else 255 if x > 255 else x


def avg_rgb(bgr_img, step):
    small = bgr_img[::step, ::step, :3]
    b, g, r = small.mean(axis=(0, 1))
    return int(r), int(g), int(b)


def scale_brightness(r, g, b, percent):
    scale = percent / 100.0
    return (
        clamp255(int(r * scale)),
        clamp255(int(g * scale)),
        clamp255(int(b * scale)),
    )


def rgb_to_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"


def make_gradient(width=640, height=18):
    # Blue -> Cyan -> Purple -> Pink
    stops = [
        (0.00, (50, 90, 220)),
        (0.35, (0, 200, 200)),
        (0.70, (120, 90, 220)),
        (1.00, (255, 0, 220)),
    ]

    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    def lerp(a, b, t):
        return int(a + (b - a) * t)

    for x in range(width):
        t = x / (width - 1)
        for i in range(len(stops) - 1):
            t0, c0 = stops[i]
            t1, c1 = stops[i + 1]
            if t0 <= t <= t1:
                local = 0 if t1 == t0 else (t - t0) / (t1 - t0)
                r = lerp(c0[0], c1[0], local)
                g = lerp(c0[1], c1[1], local)
                b = lerp(c0[2], c1[2], local)
                draw.line([(x, 0), (x, height)], fill=(r, g, b))
                break

    return img


# -----------------------
# Gradient slider (Canvas)
# -----------------------
class GradientSlider(ctk.CTkFrame):
    """
    Gradient track is the slider background.
    Range: 0..200
    """
    def __init__(self, master, width=620, height=42, initial=100, on_change=None):
        super().__init__(master, fg_color="#1b1b1b")
        self.width = width
        self.height = height
        self.on_change = on_change
        self.value = int(initial)

        self.track_w = width
        self.track_h = 18
        self.track_x = 0
        self.track_y = 8

        self.canvas = ctk.CTkCanvas(self, width=width, height=height,
                                    bg="#1b1b1b", highlightthickness=0)
        self.canvas.pack()

        grad = make_gradient(self.track_w, self.track_h)
        self.grad_img = ImageTk.PhotoImage(grad)
        self.canvas.create_image(self.track_x, self.track_y, image=self.grad_img, anchor="nw")

        self.knob_r = 12
        self.knob = None
        self.knob_outline = None

        self.label_left = self.canvas.create_text(0, 36, text="0", fill="#d0d0d0",
                                                  font=("Segoe UI", 14), anchor="w")
        self.label_mid = self.canvas.create_text(width // 2, 36, text=f"{self.value}%", fill="#d0d0d0",
                                                 font=("Segoe UI", 14), anchor="center")
        self.label_right = self.canvas.create_text(width, 36, text="200%", fill="#d0d0d0",
                                                   font=("Segoe UI", 14), anchor="e")

        self._draw_knob_from_value()

        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<B1-Motion>", self._drag)

    def _value_to_x(self, v):
        v = max(0, min(200, int(v)))
        t = v / 200.0
        return int(self.track_x + t * self.track_w)

    def _x_to_value(self, x):
        x = max(self.track_x, min(self.track_x + self.track_w, x))
        t = (x - self.track_x) / float(self.track_w)
        return int(round(t * 200))

    def _draw_knob_from_value(self):
        x = self._value_to_x(self.value)
        y = self.track_y + self.track_h // 2

        if self.knob is not None:
            self.canvas.delete(self.knob)
        if self.knob_outline is not None:
            self.canvas.delete(self.knob_outline)

        self.knob = self.canvas.create_oval(
            x - self.knob_r, y - self.knob_r,
            x + self.knob_r, y + self.knob_r,
            fill="#cfcfcf", outline=""
        )
        self.knob_outline = self.canvas.create_oval(
            x - self.knob_r, y - self.knob_r,
            x + self.knob_r, y + self.knob_r,
            outline="#9b9b9b", width=1
        )

        self.canvas.itemconfig(self.label_mid, text=f"{self.value}%")

    def set(self, v: int):
        self.value = max(0, min(200, int(v)))
        self._draw_knob_from_value()
        if self.on_change:
            self.on_change(self.value)

    def get(self) -> int:
        return self.value

    def _click(self, event):
        self.set(self._x_to_value(event.x))

    def _drag(self, event):
        self.set(self._x_to_value(event.x))


# -----------------------
# SpinBox-like widget (Entry + up/down)
# -----------------------
class EdgeRGBSpinBox(ctk.CTkFrame):
    def __init__(self, master, width=140, height=38, initial=20, min_value=0, max_value=999):
        super().__init__(master, fg_color="#1b1b1b")
        self.min_value = int(min_value)
        self.max_value = int(max_value)

        self.entry = ctk.CTkEntry(
            self, width=width, height=height,
            fg_color="#222222", text_color="#dddddd",
            border_color="#444444", corner_radius=0
        )
        self.entry.pack(side="left")
        self.entry.insert(0, str(int(initial)))

        btn_col = ctk.CTkFrame(self, fg_color="#1b1b1b")
        btn_col.pack(side="left", padx=(0, 0))

        self.btn_up = ctk.CTkButton(
            btn_col, text="▲",
            width=38, height=height // 2,
            fg_color="#2b2b2b", hover_color="#3a3a3a",
            corner_radius=0, command=self._inc
        )
        self.btn_up.pack()

        self.btn_dn = ctk.CTkButton(
            btn_col, text="▼",
            width=38, height=height // 2,
            fg_color="#2b2b2b", hover_color="#3a3a3a",
            corner_radius=0, command=self._dec
        )
        self.btn_dn.pack()

    def get_int(self, fallback=0):
        try:
            v = int(self.entry.get().strip())
        except:
            v = int(fallback)
        return max(self.min_value, min(self.max_value, v))

    def set_int(self, v: int):
        v = max(self.min_value, min(self.max_value, int(v)))
        self.entry.delete(0, "end")
        self.entry.insert(0, str(v))

    def _inc(self):
        self.set_int(self.get_int() + 1)

    def _dec(self):
        self.set_int(self.get_int() - 1)


# -----------------------
# App
# -----------------------
class EdgeRGBGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("EdgeRGB by NazeehPULLAT (3-Zone) - Arduino Uno")
        self.geometry("720x980")
        self.minsize(680, 920)
        self.configure(fg_color="#1b1b1b")

        self.running = False
        self.worker = None
        self.ser = None

        self.com_var = ctk.StringVar(value="")
        self.mon_var = ctk.StringVar(value="")
        self.brightness_var = ctk.IntVar(value=100)

        self.left_rgb = (0, 0, 0)
        self.top_rgb = (0, 0, 0)
        self.right_rgb = (0, 0, 0)

        self.monitors = []
        self.ports = []

        self._build_ui()
        self._refresh_monitors()
        self._refresh_ports()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI ----------
    def _build_ui(self):
        # Top controls (match screenshot: monitor dropdown, then com/refresh row)
        top = ctk.CTkFrame(self, fg_color="#1b1b1b")
        top.pack(fill="x", padx=22, pady=(18, 10))

        self.monitor_dropdown = ctk.CTkOptionMenu(
            top,
            values=["Monitor"],
            variable=self.mon_var,
            width=650,
            height=42,
            fg_color="#222222",
            button_color="#2b2b2b",
            button_hover_color="#3a3a3a",
            dropdown_fg_color="#222222",
            dropdown_hover_color="#333333",
            dropdown_text_color="#dddddd",
            text_color="#dddddd",
            corner_radius=0,
        )
        self.monitor_dropdown.pack(fill="x", pady=(0, 12))

        com_row = ctk.CTkFrame(top, fg_color="#1b1b1b")
        com_row.pack(fill="x")

        self.com_dropdown = ctk.CTkOptionMenu(
            com_row,
            values=["ComPort"],
            variable=self.com_var,
            width=520,
            height=40,
            fg_color="#222222",
            button_color="#2b2b2b",
            button_hover_color="#3a3a3a",
            dropdown_fg_color="#222222",
            dropdown_hover_color="#333333",
            dropdown_text_color="#dddddd",
            text_color="#dddddd",
            corner_radius=0,
        )
        self.com_dropdown.pack(side="left", fill="x", expand=True)

        self.refresh_btn = ctk.CTkButton(
            com_row,
            text="Refresh",
            width=110,
            height=40,
            fg_color="#2b2b2b",
            hover_color="#3a3a3a",
            corner_radius=0,
            command=self._refresh_ports,
        )
        self.refresh_btn.pack(side="left", padx=(12, 0))

        # Diagram area (center)
        mid = ctk.CTkFrame(self, fg_color="#1b1b1b")
        mid.pack(fill="x", padx=22, pady=(8, 14))

        self.canvas = ctk.CTkCanvas(mid, width=660, height=430, bg="#1b1b1b", highlightthickness=0)
        self.canvas.pack()
        self._draw_monitor_diagram_like_image()

        # Controls area
        controls = ctk.CTkFrame(self, fg_color="#1b1b1b")
        controls.pack(fill="x", padx=36, pady=(0, 0))

        # FPS row
        row1 = ctk.CTkFrame(controls, fg_color="#1b1b1b")
        row1.pack(fill="x", pady=(10, 6))
        ctk.CTkLabel(row1, text="FPS", text_color="#bdbdbd",
                     font=ctk.CTkFont(size=18)).pack(side="left", padx=(0, 30))
        self.fps_spin = EdgeRGBSpinBox(row1, width=140, height=38, initial=20, min_value=5, max_value=60)
        self.fps_spin.pack(side="left")

        # Quality row
        row2 = ctk.CTkFrame(controls, fg_color="#1b1b1b")
        row2.pack(fill="x", pady=(6, 6))
        ctk.CTkLabel(row2, text="Quality (STEPS)", text_color="#bdbdbd",
                     font=ctk.CTkFont(size=18)).pack(side="left", padx=(0, 30))
        self.step_spin = EdgeRGBSpinBox(row2, width=140, height=38, initial=20, min_value=5, max_value=80)
        self.step_spin.pack(side="left")
        ctk.CTkLabel(row2, text="Lower = better color, Higher = faster",
                     text_color="#4f4f4f", font=ctk.CTkFont(size=14)).pack(side="left", padx=18)

        # Brightness label + gradient slider
        row3 = ctk.CTkFrame(controls, fg_color="#1b1b1b")
        row3.pack(fill="x", pady=(24, 0))
        ctk.CTkLabel(row3, text="Brightness", text_color="#bdbdbd",
                     font=ctk.CTkFont(size=20)).pack(anchor="w")

        self.grad_slider = GradientSlider(
            controls,
            width=620,
            height=44,
            initial=self.brightness_var.get(),
            on_change=self._on_brightness
        )
        self.grad_slider.pack(pady=(6, 0))

        # Start/Stop buttons
        btn_row = ctk.CTkFrame(self, fg_color="#1b1b1b")
        btn_row.pack(fill="x", padx=36, pady=(26, 10))

        self.start_btn = ctk.CTkButton(
            btn_row, text="START",
            width=300, height=80,
            fg_color="#2f58d6", hover_color="#3a66ee",
            corner_radius=0,
            font=ctk.CTkFont(size=30, weight="bold"),
            command=self.start
        )
        self.start_btn.pack(side="left", padx=(0, 22))

        self.stop_btn = ctk.CTkButton(
            btn_row, text="STOP",
            width=300, height=80,
            fg_color="#ff00dc", hover_color="#ff33e4",
            text_color="#ffffff",
            corner_radius=0,
            font=ctk.CTkFont(size=30, weight="bold"),
            command=self.stop,
            state="disabled"
        )
        self.stop_btn.pack(side="right")

        # Bottom status bar (v.1.08 left + STATUS: green + message gray)
        self.status_frame = ctk.CTkFrame(self, height=48, fg_color="#212121", corner_radius=0)
        self.status_frame.pack(fill="x", side="bottom")

        self.version_label = ctk.CTkLabel(
            self.status_frame, text="v.1.08",
            text_color="#8e8e8e", font=ctk.CTkFont(size=14)
        )
        self.version_label.pack(side="left", padx=10)

        self.status_mid = ctk.CTkFrame(self.status_frame, fg_color="#212121")
        self.status_mid.pack(side="left", expand=True)

        self.status_prefix = ctk.CTkLabel(
            self.status_mid, text="STATUS:",
            text_color="#7CFC00", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.status_prefix.pack(side="left")

        self.status_msg = ctk.CTkLabel(
            self.status_mid, text=" Idle.",
            text_color="#9a9a9a", font=ctk.CTkFont(size=14)
        )
        self.status_msg.pack(side="left")

    def _draw_monitor_diagram_like_image(self):
        """
        Match the screenshot:
        - gray monitor outline
        - magenta top bar and left/right bars
        - simple stand + base
        """
        self.canvas.delete("all")

        # Monitor outline (outer)
        outer = (150, 75, 510, 290)
        self.canvas.create_rectangle(*outer, outline="#a0a0a0", width=6)

        # Screen inside
        inner = (170, 95, 490, 270)
        self.canvas.create_rectangle(*inner, outline="#7a7a7a", width=2, fill="#242424")

        # Stand + base
        self.canvas.create_rectangle(318, 290, 342, 345, fill="#a9a9a9", outline="")
        self.canvas.create_rectangle(250, 335, 410, 360, fill="#a9a9a9", outline="")

        # LED bars (magenta by default like screenshot)
        self.top_bar = self.canvas.create_rectangle(190, 55, 470, 65, fill="#ff00dc", outline="")
        self.left_bar = self.canvas.create_rectangle(135, 105, 145, 260, fill="#ff00dc", outline="")
        self.right_bar = self.canvas.create_rectangle(515, 105, 525, 260, fill="#ff00dc", outline="")

        self._update_bar_colors()

    def _update_bar_colors(self):
        # Live colors while running (otherwise keep magenta-ish look from screenshot)
        if self.running:
            self.canvas.itemconfig(self.left_bar, fill=rgb_to_hex(*self.left_rgb))
            self.canvas.itemconfig(self.top_bar, fill=rgb_to_hex(*self.top_rgb))
            self.canvas.itemconfig(self.right_bar, fill=rgb_to_hex(*self.right_rgb))
        else:
            self.canvas.itemconfig(self.left_bar, fill="#ff00dc")
            self.canvas.itemconfig(self.top_bar, fill="#ff00dc")
            self.canvas.itemconfig(self.right_bar, fill="#ff00dc")

    def _on_brightness(self, v):
        self.brightness_var.set(int(v))

    # ---------- Refresh data ----------
    def _refresh_ports(self):
        self.ports = list_com_ports()
        values = self.ports if self.ports else ["No COM ports"]
        self.com_dropdown.configure(values=values)
        if self.ports:
            if self.com_var.get() not in self.ports:
                self.com_var.set(self.ports[0])
        else:
            self.com_var.set(values[0])

    def _refresh_monitors(self):
        self.monitors = list_monitors()
        values = [label for (_, label) in self.monitors] if self.monitors else ["No monitors found"]
        self.monitor_dropdown.configure(values=values)
        if self.monitors:
            self.mon_var.set(values[0])
        else:
            self.mon_var.set(values[0])

    def _selected_monitor_index(self):
        current = self.mon_var.get()
        for idx, label in self.monitors:
            if label == current:
                return idx
        return 1

    def _set_status(self, msg: str):
        self.status_msg.configure(text=f" {msg}")

    # ---------- Start/Stop ----------
    def start(self):
        if self.running:
            return

        port = self.com_var.get().strip()
        if not port or "No COM" in port:
            messagebox.showerror("COM Port", "Please select a valid COM port.")
            return

        mon_idx = self._selected_monitor_index()

        fps = self.fps_spin.get_int(fallback=20)
        step = self.step_spin.get_int(fallback=20)

        fps = max(5, min(60, fps))
        step = max(5, min(80, step))

        # Open serial
        try:
            self.ser = serial.Serial(port, 115200, timeout=1, write_timeout=1, dsrdtr=False, rtscts=False)
            self.ser.setDTR(False)
            time.sleep(1)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.ser.setDTR(True)
            time.sleep(2)
        except Exception as e:
            messagebox.showerror("Serial Error", f"Could not open {port}\n\n{e}")
            self.ser = None
            return

        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_status(f"Running on {port}, monitor {mon_idx}....")
        self._update_bar_colors()

        self.worker = threading.Thread(target=self._loop, args=(mon_idx, fps, step), daemon=True)
        self.worker.start()

    def stop(self):
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._set_status("Stopped.")
        self._update_bar_colors()

        try:
            if self.ser:
                self.ser.close()
        except:
            pass
        self.ser = None

    def _loop(self, mon_idx, fps, step):
        try:
            with mss() as sct:
                mon = sct.monitors[mon_idx]
                x, y, w, h = mon["left"], mon["top"], mon["width"], mon["height"]

                zones = {
                    "LEFT":  {"left": x,              "top": y, "width": w // 4,       "height": h},
                    "TOP":   {"left": x,              "top": y, "width": w,            "height": h // 4},
                    "RIGHT": {"left": x + w * 3 // 4, "top": y, "width": w // 4,       "height": h},
                }

                delay = 1.0 / fps

                while self.running and self.ser:
                    t0 = time.time()

                    left_img = np.array(sct.grab(zones["LEFT"]))
                    top_img = np.array(sct.grab(zones["TOP"]))
                    right_img = np.array(sct.grab(zones["RIGHT"]))

                    rL, gL, bL = avg_rgb(left_img, step)
                    rT, gT, bT = avg_rgb(top_img, step)
                    rR, gR, bR = avg_rgb(right_img, step)

                    bright = self.brightness_var.get()
                    rL, gL, bL = scale_brightness(rL, gL, bL, bright)
                    rT, gT, bT = scale_brightness(rT, gT, bT, bright)
                    rR, gR, bR = scale_brightness(rR, gR, bR, bright)

                    line = f"{rL},{gL},{bL}|{rT},{gT},{bT}|{rR},{gR},{bR}\n"
                    self.ser.write(line.encode("ascii"))

                    self.left_rgb = (rL, gL, bL)
                    self.top_rgb = (rT, gT, bT)
                    self.right_rgb = (rR, gR, bR)

                    self.after(0, self._update_bar_colors)

                    dt = time.time() - t0
                    if dt < delay:
                        time.sleep(delay - dt)

        except Exception as e:
            self.after(0, lambda: self._set_status(f"Error: {e}"))
        finally:
            self.after(0, self.stop)

    def on_close(self):
        self.stop()
        self.destroy()


if __name__ == "__main__":
    EdgeRGBGUI().mainloop()
