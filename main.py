import logging
import requests
import threading
import tkinter as tk
import io
import re
from tkinter import ttk, messagebox
from bs4 import BeautifulSoup, Tag
from typing import List, Optional, Dict
from dataclasses import dataclass

# --- Import Image Handling ---
try:
    from PIL import Image, ImageTk
except ImportError:
    print("CRITICAL ERROR: 'Pillow' library is missing.")
    print("Please install it by running: pip install pillow")
    exit()

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# --- Data Models ---

@dataclass
class GameItem:
    """Represents a single item within a build."""
    name: str
    image_url: str
    count: int = 1
    image_data: Optional[bytes] = None 

@dataclass
class BuildRow:
    """Represents a row of data (items + stats)."""
    items: List[GameItem]
    win_rate: str
    pick_rate: str
    games: str

# --- Helper Functions ---

def normalize_champion_name(name: str) -> str:
    """
    Normalizes champion names for OP.GG URLs.
    Rules:
    1. Lowercase everything.
    2. Remove spaces, apostrophes, periods, and ampersands.
    
    Examples: 
    - "Vel'Koz" -> "velkoz"
    - "Dr. Mundo" -> "drmundo"
    - "Lee Sin" -> "leesin"
    - "Nunu & Willump" -> "nunuwillump"
    """
    # Convert to lower case
    clean = name.lower()
    # Remove any character that is NOT a lowercase letter (a-z)
    # This handles spaces, ', ., &, numbers, etc.
    clean = re.sub(r'[^a-z]', '', clean)
    
    # Edge case: Nunu & Willump is often just 'nunu' on some sites, 
    # but op.gg usually accepts 'nunu' or redirects. 
    # 'wukong' is safe (vs MonkeyKing).
    return clean

# --- Scraper Logic ---

class OpGgAramScraper:
    
    BASE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.BASE_HEADERS)

    def fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        try:
            logger.info(f"Requesting: {url}")
            response = self.session.get(url, timeout=10)
            
            # OP.GG might redirect to a 404 page content but return 200, 
            # or return 404 status.
            if response.status_code == 404:
                return None
                
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.RequestException as e:
            logger.error(f"Failed to fetch data from {url}: {e}")
            return None

    def fetch_image_bytes(self, url: str) -> Optional[bytes]:
        try:
            if url.startswith("//"):
                url = "https:" + url
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                return resp.content
        except Exception as e:
            logger.warning(f"Could not download image {url}: {e}")
        return None

    def _extract_item_details(self, img_tag: Tag) -> GameItem:
        name = img_tag.get('alt', 'Unknown Item')
        src = img_tag.get('src', '')
        count = 1

        parent_div = img_tag.find_parent("div", class_="relative")
        if parent_div:
            count_div = parent_div.find("div", class_="absolute")
            if count_div:
                try:
                    count = int(count_div.get_text(strip=True))
                except ValueError:
                    count = 1
        
        return GameItem(name=name, image_url=src, count=count)

    def _extract_table_by_header(self, soup: BeautifulSoup, header_text: str) -> List[BuildRow]:
        data = []
        # Case insensitive search for header
        header = soup.find(lambda tag: tag.name == "th" and header_text.lower() in tag.get_text().lower())
        
        if not header:
            return []

        table = header.find_parent("table")
        if not table: return []
        tbody = table.find("tbody")
        if not tbody: return []
            
        rows = tbody.find_all("tr")

        for row in rows:
            cols = row.find_all("td")
            if not cols or len(cols) < 3:
                continue

            item_container = cols[0]
            images = item_container.find_all("img")
            items_list = [self._extract_item_details(img) for img in images if img.get('src')]

            stats_div = cols[1]
            pick_rate = stats_div.find("strong").get_text(strip=True) if stats_div.find("strong") else "N/A"
            games = stats_div.find("span").get_text(strip=True) if stats_div.find("span") else "N/A"

            win_rate_div = cols[2]
            win_rate = win_rate_div.find("strong").get_text(strip=True) if win_rate_div.find("strong") else "N/A"

            data.append(BuildRow(items=items_list, win_rate=win_rate, pick_rate=pick_rate, games=games))

        return data

    def get_all_data(self, soup: BeautifulSoup) -> Dict[str, List[BuildRow]]:
        results = {
            "Core Builds": self._extract_table_by_header(soup, "Core Builds"),
            "Starter Items": self._extract_table_by_header(soup, "Starter Items"),
            "Boots": self._extract_table_by_header(soup, "Boots"),
            "Skills": self._extract_table_by_header(soup, "Skill"),
        }
        
        # Pre-fetch images
        all_rows = [row for cat in results.values() for row in cat]
        for row in all_rows:
            for item in row.items:
                if item.image_url:
                    item.image_data = self.fetch_image_bytes(item.image_url)
        return results

# --- GUI Components ---

class ScrollableFrame(ttk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

class AramBuildApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OP.GG ARAM Visualizer")
        self.root.geometry("1100x800")
        
        self.scraper = OpGgAramScraper()
        self.photo_refs = [] 
        
        self._setup_styles()
        self._build_layout()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Bold.TLabel", font=('Segoe UI', 10, 'bold'))
        style.configure("Header.TLabel", font=('Segoe UI', 11, 'bold'), background="#d1d5db")

    def _build_layout(self):
        # 1. Top Control Bar
        control_frame = ttk.Frame(self.root, padding="10")
        control_frame.pack(fill=tk.X)

        ttk.Label(control_frame, text="Champion Name:", style="Bold.TLabel").pack(side=tk.LEFT, padx=(0, 5))
        
        self.champ_var = tk.StringVar(value="Vel'Koz")
        self.champ_entry = ttk.Entry(control_frame, textvariable=self.champ_var, width=25, font=('Segoe UI', 10))
        self.champ_entry.pack(side=tk.LEFT, padx=5)
        # Bind Enter key to trigger search
        self.champ_entry.bind("<Return>", lambda event: self.on_fetch_click())

        self.fetch_btn = ttk.Button(control_frame, text="Search", command=self.on_fetch_click)
        self.fetch_btn.pack(side=tk.LEFT, padx=5)

        self.status_lbl = ttk.Label(control_frame, text="Ready", foreground="gray")
        self.status_lbl.pack(side=tk.LEFT, padx=15)

        # 2. Main Tab Area
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.tabs = {}
        for category in ["Core Builds", "Starter Items", "Boots", "Skills"]:
            frame = ScrollableFrame(self.notebook)
            self.notebook.add(frame, text=category)
            self.tabs[category] = frame.scrollable_frame

    def on_fetch_click(self):
        raw_name = self.champ_var.get().strip()
        if not raw_name: return

        # 1. Normalize Name
        clean_name = normalize_champion_name(raw_name)
        
        # 2. Construct URL
        # Pattern: https://op.gg/lol/modes/aram/{name}/build
        url = f"https://op.gg/lol/modes/aram/{clean_name}/build"

        self.fetch_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text=f"Searching for '{clean_name}'...", foreground="blue")
        
        # Clear UI
        self.photo_refs.clear()
        for frame in self.tabs.values():
            for widget in frame.winfo_children():
                widget.destroy()

        # Start thread
        threading.Thread(target=self._worker_thread, args=(url,), daemon=True).start()

    def _worker_thread(self, url: str):
        soup = self.scraper.fetch_page(url)
        
        if not soup:
            self.root.after(0, self._handle_error)
            return
            
        # Check if we actually landed on a build page (basic validation)
        # Often if the champ doesn't exist, OP.GG might redirect to home or show 404
        if "Core Builds" not in soup.text and "Starter Items" not in soup.text:
             self.root.after(0, self._handle_not_found)
             return

        results = self.scraper.get_all_data(soup)
        self.root.after(0, lambda: self._update_ui(results))

    def _handle_error(self):
        self.status_lbl.config(text="Connection Error", foreground="red")
        messagebox.showerror("Error", "Could not connect to OP.GG. Check internet.")
        self.fetch_btn.config(state=tk.NORMAL)

    def _handle_not_found(self):
        self.status_lbl.config(text="Champion Not Found", foreground="red")
        messagebox.showwarning("Not Found", "Could not find ARAM data for this champion.\nCheck the spelling.")
        self.fetch_btn.config(state=tk.NORMAL)

    def _update_ui(self, results: Dict[str, List[BuildRow]]):
        for category, rows in results.items():
            parent_frame = self.tabs[category]
            
            # Header
            header_frame = ttk.Frame(parent_frame, style="Header.TLabel", padding=5)
            header_frame.pack(fill=tk.X, pady=(0, 5))
            
            ttk.Label(header_frame, text="Win Rate", width=10, style="Header.TLabel").grid(row=0, column=0, padx=5)
            ttk.Label(header_frame, text="Pick Rate", width=10, style="Header.TLabel").grid(row=0, column=1, padx=5)
            ttk.Label(header_frame, text="Games", width=10, style="Header.TLabel").grid(row=0, column=2, padx=5)
            ttk.Label(header_frame, text="Items", style="Header.TLabel").grid(row=0, column=3, padx=20, sticky="w")

            if not rows:
                ttk.Label(parent_frame, text="No data found.").pack(pady=10)
                continue

            for i, row_data in enumerate(rows):
                row_frame = ttk.Frame(parent_frame)
                row_frame.pack(fill=tk.X, pady=5, padx=5)
                bg_color = "#f3f4f6" if i % 2 == 0 else "white"
                
                # Stats
                tk.Label(row_frame, text=row_data.win_rate, width=12, bg=bg_color, font=('Segoe UI', 10, 'bold'), fg="blue").grid(row=0, column=0, padx=5, ipady=10)
                tk.Label(row_frame, text=row_data.pick_rate, width=12, bg=bg_color, font=('Segoe UI', 10)).grid(row=0, column=1, padx=5, ipady=10)
                tk.Label(row_frame, text=row_data.games, width=12, bg=bg_color, font=('Segoe UI', 10), fg="gray").grid(row=0, column=2, padx=5, ipady=10)

                # Items
                items_frame = tk.Frame(row_frame, bg=bg_color)
                items_frame.grid(row=0, column=3, padx=20, sticky="w")

                for item in row_data.items:
                    item_wrapper = tk.Frame(items_frame, bg=bg_color)
                    item_wrapper.pack(side=tk.LEFT, padx=4)

                    if item.image_data:
                        try:
                            pil_img = Image.open(io.BytesIO(item.image_data))
                            pil_img = pil_img.resize((40, 40), Image.Resampling.LANCZOS)
                            tk_img = ImageTk.PhotoImage(pil_img)
                            self.photo_refs.append(tk_img)
                            
                            tk.Label(item_wrapper, image=tk_img, bg=bg_color).pack()
                            if item.count > 1:
                                count_lbl = tk.Label(item_wrapper, text=f"x{item.count}", bg="black", fg="white", font=("Arial", 8, "bold"))
                                count_lbl.place(relx=1.0, rely=1.0, anchor="se")
                        except Exception:
                            tk.Label(item_wrapper, text="?", bg=bg_color).pack()
                    else:
                        tk.Label(item_wrapper, text=item.name[:5], bg=bg_color).pack()

        self.status_lbl.config(text="Success", foreground="green")
        self.fetch_btn.config(state=tk.NORMAL)


if __name__ == "__main__":
    root = tk.Tk()
    app = AramBuildApp(root)
    root.mainloop()