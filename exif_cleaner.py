import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import io
from PIL import Image
import piexif

SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp")

BG = "#1e1e2e"
SURFACE = "#2a2a3e"
ACCENT = "#7c3aed"
ACCENT_HOVER = "#6d28d9"
TEXT = "#e2e8f0"
TEXT_MUTED = "#94a3b8"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
DANGER = "#ef4444"
BORDER = "#3f3f5f"


def get_metadata(image_path: str) -> dict:
    meta = {}
    try:
        img = Image.open(image_path)
        info = img.info or {}

        # Basic info
        meta["Formato"] = img.format or "Desconhecido"
        meta["Modo de Cor"] = img.mode
        meta["Dimensões"] = f"{img.width} x {img.height} px"

        # EXIF data
        if "exif" in info:
            try:
                exif_dict = piexif.load(info["exif"])
                for ifd_name in exif_dict:
                    if ifd_name == "thumbnail":
                        if exif_dict[ifd_name]:
                            meta["Thumbnail EXIF"] = "Presente"
                        continue
                    for tag_id, value in exif_dict[ifd_name].items():
                        tag_name = piexif.TAGS[ifd_name].get(tag_id, {}).get("name", f"Tag_{tag_id}")
                        if isinstance(value, bytes):
                            try:
                                display = value.decode("utf-8", errors="ignore").strip("\x00")
                            except Exception:
                                display = f"<binário {len(value)} bytes>"
                        elif isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
                            display = f"{value[0]}/{value[1]}"
                        else:
                            display = str(value)
                        if display:
                            meta[tag_name] = display[:120]
            except Exception as e:
                meta["EXIF (erro ao ler)"] = str(e)

        # Other metadata chunks
        for key in ("dpi", "icc_profile", "photoshop", "xmp", "comment", "artist",
                    "copyright", "software", "make", "model", "gps_ifd"):
            if key in info:
                val = info[key]
                if isinstance(val, bytes):
                    meta[key.upper()] = f"<dados binários {len(val)} bytes>"
                elif val:
                    meta[key.upper()] = str(val)[:120]

        if "dpi" in info:
            meta["DPI"] = str(info["dpi"])

        img.close()
    except Exception as e:
        meta["Erro"] = str(e)

    return meta


def clean_image(input_path: str, output_path: str) -> tuple[bool, list[str]]:
    cleaned = []
    try:
        img = Image.open(input_path)
        info = img.info or {}

        # Track what was removed
        if "exif" in info:
            cleaned.append("Dados EXIF (câmera, GPS, configurações)")
        if "icc_profile" in info:
            cleaned.append("Perfil ICC de cor")
        if "xmp" in info:
            cleaned.append("Metadados XMP")
        if "photoshop" in info:
            cleaned.append("Dados Photoshop")
        if "comment" in info:
            cleaned.append("Comentários embutidos")
        for k in ("dpi", "artist", "copyright", "software"):
            if k in info:
                cleaned.append(f"Campo: {k.upper()}")

        # Convert to RGB/RGBA if needed to allow re-saving cleanly
        fmt = (img.format or "PNG").upper()
        if fmt in ("JPEG", "JPG"):
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(output_path, format="JPEG", quality=95)
        elif fmt == "PNG":
            new_img = Image.new(img.mode, img.size)
            new_img.putdata(list(img.getdata()))
            new_img.save(output_path, format="PNG", optimize=True)
        elif fmt == "WEBP":
            img.save(output_path, format="WEBP", quality=95)
        elif fmt == "GIF":
            img.save(output_path, format="GIF")
        elif fmt in ("TIFF", "TIF"):
            img.save(output_path, format="TIFF")
        elif fmt == "BMP":
            img.save(output_path, format="BMP")
        else:
            img.save(output_path)

        img.close()

        if not cleaned:
            cleaned.append("Nenhum metadado detectado — imagem já estava limpa")

        return True, cleaned
    except Exception as e:
        return False, [f"Erro: {e}"]


class ExifCleanerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EXIF Cleaner — Limpador de Metadados")
        self.root.geometry("780x640")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self.root.minsize(620, 500)

        self.current_file: str | None = None

        self._build_ui()

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=ACCENT, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="🧹 EXIF Cleaner", font=("Segoe UI", 18, "bold"),
                 bg=ACCENT, fg="white").pack()
        tk.Label(header, text="Remova todos os metadados das suas imagens",
                 font=("Segoe UI", 10), bg=ACCENT, fg="#ddd6fe").pack()

        # Drop zone / file selector
        drop_frame = tk.Frame(self.root, bg=BG, pady=12, padx=20)
        drop_frame.pack(fill="x")

        self.drop_label = tk.Label(
            drop_frame,
            text="Nenhuma imagem carregada",
            font=("Segoe UI", 11),
            bg=SURFACE,
            fg=TEXT_MUTED,
            relief="flat",
            bd=0,
            pady=20,
            padx=20,
            cursor="hand2",
        )
        self.drop_label.pack(fill="x")
        self.drop_label.bind("<Button-1>", lambda e: self.load_image())

        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=(0, 12))

        self._btn(btn_row, "📂  Carregar Imagem", self.load_image, ACCENT).pack(side="left", padx=6)
        self._btn(btn_row, "🧹  Limpar Metadados", self.clean, SUCCESS, state="disabled",
                  attr="_clean_btn").pack(side="left", padx=6)
        self._btn(btn_row, "🔄  Resetar", self.reset, SURFACE).pack(side="left", padx=6)

        # Metadata panel
        meta_outer = tk.Frame(self.root, bg=BG, padx=20)
        meta_outer.pack(fill="both", expand=True)

        tk.Label(meta_outer, text="Metadados encontrados:", font=("Segoe UI", 10, "bold"),
                 bg=BG, fg=TEXT_MUTED, anchor="w").pack(fill="x", pady=(0, 4))

        table_frame = tk.Frame(meta_outer, bg=SURFACE, relief="flat", bd=0)
        table_frame.pack(fill="both", expand=True)

        cols = ("Campo", "Valor")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                                  selectmode="none", height=14)
        self.tree.heading("Campo", text="Campo")
        self.tree.heading("Valor", text="Valor")
        self.tree.column("Campo", width=200, anchor="w", stretch=False)
        self.tree.column("Valor", width=480, anchor="w")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._style_tree()

        # Status bar
        self.status_var = tk.StringVar(value="Pronto. Carregue uma imagem para começar.")
        status_bar = tk.Label(self.root, textvariable=self.status_var,
                               bg=SURFACE, fg=TEXT_MUTED,
                               font=("Segoe UI", 9), anchor="w", padx=12, pady=6)
        status_bar.pack(fill="x", side="bottom")

    def _btn(self, parent, text, cmd, color, state="normal", attr=None):
        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=color, fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0,
            padx=16, pady=8,
            cursor="hand2",
            activebackground=ACCENT_HOVER,
            activeforeground="white",
            state=state,
        )
        if attr:
            setattr(self, attr, btn)
        return btn

    def _style_tree(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview",
                        background=SURFACE,
                        foreground=TEXT,
                        rowheight=26,
                        fieldbackground=SURFACE,
                        borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading",
                        background=BG,
                        foreground=TEXT_MUTED,
                        relief="flat",
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])

    def load_image(self):
        path = filedialog.askopenfilename(
            title="Selecione uma imagem",
            filetypes=[
                ("Imagens", "*.jpg *.jpeg *.png *.bmp *.gif *.tiff *.tif *.webp"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTS:
            messagebox.showwarning("Formato não suportado",
                                   f"O formato '{ext}' pode não ser suportado.")

        self.current_file = path
        filename = os.path.basename(path)
        size_kb = os.path.getsize(path) / 1024

        self.drop_label.config(
            text=f"✅  {filename}   ({size_kb:.1f} KB)",
            fg=TEXT,
            bg=SURFACE,
        )

        self._populate_metadata(path)
        self._clean_btn.config(state="normal")
        self.status_var.set(f"Imagem carregada: {path}")

    def _populate_metadata(self, path: str):
        for row in self.tree.get_children():
            self.tree.delete(row)

        meta = get_metadata(path)
        if not meta:
            self.tree.insert("", "end", values=("(sem metadados)", ""))
            return

        for i, (key, val) in enumerate(meta.items()):
            tag = "even" if i % 2 == 0 else "odd"
            self.tree.insert("", "end", values=(key, val), tags=(tag,))

        self.tree.tag_configure("even", background=SURFACE)
        self.tree.tag_configure("odd", background="#252535")

        count = len(meta)
        self.status_var.set(f"{count} campo(s) de metadados encontrado(s).")

    def clean(self):
        if not self.current_file:
            return

        base, ext = os.path.splitext(self.current_file)
        default_name = os.path.basename(base) + "_limpo" + ext

        out_path = filedialog.asksaveasfilename(
            title="Salvar imagem limpa",
            initialfile=default_name,
            defaultextension=ext,
            filetypes=[("Mesmo formato", f"*{ext}"), ("Todos os arquivos", "*.*")],
        )
        if not out_path:
            return

        ok, removed = clean_image(self.current_file, out_path)

        if ok:
            msg = "Metadados removidos:\n\n• " + "\n• ".join(removed)
            msg += f"\n\nImagem salva em:\n{out_path}"
            messagebox.showinfo("Limpeza concluída!", msg)
            self.status_var.set(f"✅ Imagem limpa salva em: {out_path}")

            # Reload metadata view for saved file
            self.current_file = out_path
            self.drop_label.config(
                text=f"✅  {os.path.basename(out_path)}   (limpo)",
                fg=SUCCESS,
            )
            self._populate_metadata(out_path)
        else:
            messagebox.showerror("Erro", "\n".join(removed))
            self.status_var.set("Erro ao limpar imagem.")

    def reset(self):
        self.current_file = None
        self.drop_label.config(
            text="Nenhuma imagem carregada",
            fg=TEXT_MUTED,
            bg=SURFACE,
        )
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._clean_btn.config(state="disabled")
        self.status_var.set("Pronto. Carregue uma imagem para começar.")


if __name__ == "__main__":
    root = tk.Tk()
    app = ExifCleanerApp(root)
    root.mainloop()
