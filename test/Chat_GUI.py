import tkinter as tk
import tkinter.font as tkfont
import socket
import threading

class ChatApp:
    def __init__(self, local_ip, remote_ip, port=12345):
        self.mainWindow = tk.Tk()
        self.local_ip = local_ip
        self.remote_ip = remote_ip
        self.port = port
        self.running = True
        self.message_y = 10  # Starting y-position for messages
        self.messages = []  # Store message details: (text, tag, canvas_ids, replied_to)
        self.reply_to = None  # Message index being replied to
        self.init_ui()
        self.start_receiver()
        self.mainWindow.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.mainWindow.mainloop()

    def init_ui(self):
        self.mainWindow.title("Chat Application")
        self.mainWindow.geometry("400x500")
        self.mainWindow.resizable(False, False)

        # Configure grid layout for main window
        self.mainWindow.grid_rowconfigure(0, weight=1)  # Chat frame expands
        self.mainWindow.grid_rowconfigure(1, weight=0)  # Input frame fixed
        self.mainWindow.grid_columnconfigure(0, weight=1)

        # Frame for chat display
        self.chat_frame = tk.Frame(self.mainWindow)
        self.chat_frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="nsew")

        # Canvas for chat display
        self.chat_canvas = tk.Canvas(self.chat_frame, bg="white")
        self.chat_canvas.pack(fill=tk.BOTH, expand=True)
        # Add scrollbar
        self.scrollbar = tk.Scrollbar(self.chat_frame, orient=tk.VERTICAL, command=self.chat_canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_canvas.configure(yscrollcommand=self.scrollbar.set)
        # Bind canvas resizing to update scroll region
        self.chat_canvas.bind("<Configure>", lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all")))

        # Font for text measurement
        self.text_font = tkfont.Font(family="TkDefaultFont", size=9)

        # Frame for input area
        self.input_frame = tk.Frame(self.mainWindow)
        self.input_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")

        # Message input
        self.message_input = tk.Entry(self.input_frame)
        self.message_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.message_input.bind('<Return>', lambda event: self.send_message())

        # Send button
        self.send_button = tk.Button(self.input_frame, text="Send", command=self.send_message)
        self.send_button.pack(side=tk.RIGHT)

    def create_rounded_rectangle(self, x1, y1, x2, y2, radius=10, **kwargs):
        """Draw a rounded rectangle on the canvas with specified radius."""
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1, x2, y1 + radius,
            x2, y2 - radius,
            x2, y2, x2 - radius, y2,
            x1 + radius, y2,
            x1, y2, x1, y2 - radius,
            x1, y1 + radius,
            x1, y1, x1 + radius, y1
        ]
        return self.chat_canvas.create_polygon(points, smooth=True, **kwargs)

    def start_receiver(self):
        self.receiver_thread = threading.Thread(target=self.receive_messages, daemon=True)
        self.receiver_thread.start()

    def receive_messages(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server_socket.bind((self.local_ip, self.port))
            server_socket.listen(1)
            while self.running:
                client_socket, addr = server_socket.accept()
                with client_socket:
                    data = client_socket.recv(1024).decode('utf-8')
                    if data:
                        self.mainWindow.after(0, self.display_message, f"{addr[0]}: {data}", "other")
        except Exception as e:
            self.mainWindow.after(0, self.display_message, f"Error: {e}", "other")
        finally:
            server_socket.close()

    def send_message(self):
        message = self.message_input.get().strip()
        if not message:
            return
        # Check if replying to own message
        if self.reply_to is not None and 0 <= self.reply_to < len(self.messages) and self.messages[self.reply_to][1] == "you":
            # Local display only, no socket send
            self.mainWindow.after(0, self.display_message, f"You: {message}", "you", self.reply_to)
        else:
            # Send via socket
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                    client_socket.connect((self.remote_ip, self.port))
                    client_socket.sendall(message.encode('utf-8'))
                    self.mainWindow.after(0, self.display_message, f"You: {message}", "you", self.reply_to)
            except Exception as e:
                self.mainWindow.after(0, self.display_message, f"Error sending message: {e}", "other")
        self.message_input.delete(0, tk.END)
        self.reply_to = None

    def set_reply_to(self, message_index):
        """Set the message to reply to."""
        self.reply_to = message_index
        self.message_input.focus_set()

    def display_message(self, message, tag, replied_to=None):
        # Calculate bubble width (~2/3 of frame, ~380px frame width -> 253px bubble)
        bubble_width = 253
        frame_width = 380  # Frame width after padx=10
        padding = 5
        max_text_width = bubble_width - 2 * padding  # Text area inside bubble
        canvas_ids = []

        # Handle replied-to message (only for "you" messages)
        reply_text = ""
        reply_height = 0
        if tag == "you" and replied_to is not None and 0 <= replied_to < len(self.messages):
            reply_text = self.messages[replied_to][0].split('\n')[0]  # First line
            reply_text = reply_text[:30] + "..." if len(reply_text) > 30 else reply_text
            reply_height = 20 + 2 * padding  # Height for reply box (1 line + padding)

        # Wrap message text
        wrapped_lines = []
        current_line = ""
        words = message.split()
        for word in words:
            test_line = current_line + word + " "
            test_width = self.text_font.measure(test_line)
            if test_width <= max_text_width:
                current_line = test_line
            else:
                if current_line:
                    wrapped_lines.append(current_line.strip())
                current_line = word + " "
        if current_line:
            wrapped_lines.append(current_line.strip())

        # Calculate bubble dimensions
        line_height = 20
        bubble_height = len(wrapped_lines) * line_height + 2 * padding + reply_height
        bubble_x1 = 10 if tag == "you" else frame_width - bubble_width - 10
        bubble_x2 = bubble_x1 + bubble_width
        bubble_y1 = self.message_y
        bubble_y2 = self.message_y + bubble_height
        text_x = (bubble_x1 + padding) if tag == "you" else (bubble_x2 - padding)
        text_y = self.message_y + padding + reply_height

        # Draw rounded bubble
        border_color = "blue" if tag == "you" else "green"
        fill_color = "#e6f3ff" if tag == "you" else "#e6ffe6"
        text_color = "blue" if tag == "you" else "green"
        bubble_id = self.create_rounded_rectangle(
            bubble_x1, bubble_y1, bubble_x2, bubble_y2,
            radius=10, outline=border_color, width=1, fill=fill_color
        )
        canvas_ids.append(bubble_id)

        # Draw reply box if applicable
        if reply_text:
            reply_x1 = bubble_x1 + padding
            reply_x2 = bubble_x2 - padding
            reply_y1 = self.message_y + padding
            reply_y2 = reply_y1 + reply_height - padding
            reply_id = self.create_rounded_rectangle(
                reply_x1, reply_y1, reply_x2, reply_y2,
                radius=5, outline="#808080", width=1, fill="#f0f0f0"
            )
            canvas_ids.append(reply_id)
            reply_text_id = self.chat_canvas.create_text(
                reply_x1 + padding, reply_y1 + padding,
                text=reply_text, anchor="nw",
                fill="gray", width=max_text_width - 2 * padding, font=self.text_font
            )
            canvas_ids.append(reply_text_id)

        # Draw wrapped message text
        for i, line in enumerate(wrapped_lines):
            text_id = self.chat_canvas.create_text(
                text_x, text_y + i * line_height,
                text=line, anchor="nw" if tag == "you" else "ne",
                fill=text_color, width=max_text_width, font=self.text_font
            )
            canvas_ids.append(text_id)

        # Draw reply button
        button_radius = 8
        button_size = 16
        button_x = (bubble_x2 + 5) if tag == "you" else (bubble_x1 - 5 - button_size)
        button_y = bubble_y1 + bubble_height / 2 - button_size / 2
        button_id = self.create_rounded_rectangle(
            button_x, button_y, button_x + button_size, button_y + button_size,
            radius=button_radius, outline="gray", width=1, fill="lightgray"
        )
        canvas_ids.append(button_id)
        # Add reply icon (simplified as text "R")
        text_id = self.chat_canvas.create_text(
            button_x + button_size / 2, button_y + button_size / 2,
            text="R", fill="black", font=self.text_font
        )
        canvas_ids.append(text_id)
        # Bind click event
        message_index = len(self.messages)
        for cid in [button_id, text_id]:
            self.chat_canvas.tag_bind(cid, "<Button-1>", lambda e: self.set_reply_to(message_index))

        # Store message
        self.messages.append((message, tag, canvas_ids, replied_to))

        # Update y-position and scroll region
        self.message_y = bubble_y2 + 5
        self.chat_canvas.configure(scrollregion=(0, 0, frame_width, self.message_y))
        self.chat_canvas.yview_moveto(1.0)

    def on_closing(self):
        self.running = False
        self.mainWindow.destroy()

if __name__ == "__main__":
    # Configure IP addresses
    LOCAL_IP = "192.168.1.7"  # Replace with Laptop 1's IP
    REMOTE_IP = "192.168.1.13"  # Replace with Laptop 2's IP
    PORT = 12345  # Same port for both laptops

    app = ChatApp(LOCAL_IP, REMOTE_IP, PORT)