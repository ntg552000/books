#!/usr/bin/env python3
"""
pdf2epub.py - Chuyển PDF (có text layer) sang EPUB sạch để đọc trên Kindle.

Tính năng:
  - Tự xóa header/footer lặp lại (như https://thuviensach.vn) và số trang
  - Nối các dòng bị ngắt thành đoạn văn, kể cả đoạn bị cắt qua trang
  - Giữ chữ in nghiêng / in đậm
  - Nhận diện tiêu đề thông minh (hỗ trợ cả font đồng cỡ như Chương I, Quyển Một...), tách chương chuẩn, tạo mục lục
  - Bỏ qua trang mục lục in sẵn để không bị tạo chương rỗng
  - Tùy chọn gọi Calibre để ra AZW3

Cài đặt:
    pip install pymupdf ebooklib

Dùng:
    python pdf2epub.py sach.pdf
    python pdf2epub.py sach.pdf -o sach.epub --title "Tên sách" --author "Tác giả"
    python pdf2epub.py sach.pdf --azw3          # cần Calibre (ebook-convert)
    python pdf2epub.py sach.pdf --strip "thuviensach\\.vn" --strip "^Trang \\d+$"
"""
import argparse
import html
import re
import shutil
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
from ebooklib import epub

# Các mẫu rác luôn bị xóa (so khớp trên từng dòng)
DEFAULT_JUNK = [
    r"^\s*https?://\S+\s*$",          # dòng chỉ có URL
    r"^\s*www\.\S+\s*$",
    r"thuviensach\.vn",
    r"^\s*\d{1,4}\s*$",               # số trang trơ trọi
]
TERMINAL = tuple('.!?…"”»)’:;')
DASHES = ("-", "–", "—")

CHAPTER_PATTERNS = re.compile(
    r"^(chương\s+[ivxlcdm\d]+|quyển\s+[ivxlcdm\d]+|quyển\s+(một|hai|ba|bốn|năm|sáu)|"
    r"phần\s+[ivxlcdm\d]+|phụ lục\s+[a-z\d]+|lời tựa|phi lộ|đôi lời cùng bạn đọc|"
    r"ghi chú về thư tịch|bản đồ|mở đầu|vĩ thanh|kết thúc|epilogue|prologue|chapter\s+\d+|part\s+\d+)\b",
    re.I
)

CSS = """
body { font-family: serif; line-height: 1.5; padding: 0 5%; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
h1, h2 { text-align: center; margin: 1.5em 0 1em; page-break-after: avoid; }
h1 { font-size: 1.5em; font-weight: bold; }
h2 { font-size: 1.25em; font-weight: bold; }
"""


class Line:
    __slots__ = ("html", "plain", "x0", "x1", "y0", "y1", "size", "bold", "page")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def span_html(span):
    t = html.escape(span["text"])
    if not t.strip():
        return t
    font = span["font"].lower()
    flags = span["flags"]
    italic = bool(flags & 2) or "italic" in font or "oblique" in font
    bold = bool(flags & 16) or "bold" in font or "black" in font
    if italic:
        t = f"<i>{t}</i>"
    if bold:
        t = f"<b>{t}</b>"
    return t


def merge_tags(s):
    for tag in ("i", "b"):
        s = re.sub(rf"</{tag}>(\s*)<{tag}>", r"\1", s)
    return s


def extract_lines(doc, progress=True):
    pages = []
    n = len(doc)
    for pno in range(n):
        page = doc[pno]
        h = page.rect.height
        lines = []
        for block in page.get_text("dict", sort=True)["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                plain = "".join(s["text"] for s in ln["spans"]).strip()
                if not plain:
                    continue
                chars = sum(len(s["text"]) for s in spans)
                size = sum(s["size"] * len(s["text"]) for s in spans) / chars
                all_bold = all(
                    (s["flags"] & 16) or "bold" in s["font"].lower() for s in spans
                )
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(
                    Line(
                        html=merge_tags("".join(span_html(s) for s in ln["spans"]).strip()),
                        plain=plain,
                        x0=x0, x1=x1, y0=y0, y1=y1,
                        size=size, bold=all_bold, page=pno,
                    )
                )
        pages.append((h, lines))
        if progress and (pno + 1) % 50 == 0:
            print(f"  đọc {pno + 1}/{n} trang", file=sys.stderr)
    return pages


def norm(s):
    return re.sub(r"\d+", "#", s.strip().lower())


def remove_junk(pages, junk_patterns):
    """Xóa header/footer lặp lại + các dòng khớp mẫu rác."""
    n = len(pages)
    counter = Counter()
    for h, lines in pages:
        seen = set()
        for ln in lines:
            if ln.y0 < 0.08 * h or ln.y1 > 0.92 * h:
                seen.add(norm(ln.plain))
        counter.update(seen)
    threshold = max(3, int(0.3 * n))
    repeated = {k for k, v in counter.items() if v >= threshold}

    regs = [re.compile(p, re.I) for p in junk_patterns]
    out = []
    for h, lines in pages:
        keep = []
        for ln in lines:
            edge = ln.y0 < 0.08 * h or ln.y1 > 0.92 * h
            if edge and norm(ln.plain) in repeated:
                continue
            if any(r.search(ln.plain) for r in regs):
                continue
            keep.append(ln)
        out.append(keep)
    return out


def analyze(pages):
    all_lines = [ln for p in pages for ln in p]
    if not all_lines:
        sys.exit("Không đọc được chữ nào. PDF có thể là bản scan (cần OCR).")
    cnt = Counter()
    for ln in all_lines:
        cnt[round(ln.size, 1)] += len(ln.plain)
    body = cnt.most_common(1)[0][0]
    lefts = Counter(round(ln.x0) for ln in all_lines if abs(ln.size - body) < 0.6)
    left = lefts.most_common(1)[0][0]
    rights = sorted(ln.x1 for ln in all_lines if abs(ln.size - body) < 0.6)
    right = rights[int(len(rights) * 0.9)]
    return body, left, right


def find_toc_pages(pages):
    """Phát hiện các trang mục lục để tránh tách thành hàng chục chương rỗng."""
    toc_pages = set()
    for pno, lines in enumerate(pages):
        chap_lines = sum(
            1 for ln in lines if re.match(r"^(chương\s+[ivxlcdm\d]+|quyển\s+[ivxlcdm\d]+)\b", ln.plain, re.I)
        )
        if chap_lines >= 2:
            toc_pages.add(pno)
        elif any(re.match(r"^\s*mục\s+lục\s*$", ln.plain, re.I) for ln in lines):
            toc_pages.add(pno)
    return toc_pages


def is_major_chapter_heading(text):
    """Kiểm tra tiêu đề này có phải cấp độ tạo file chương mới trong EPUB không."""
    t = text.strip()
    if re.match(r"^(chương\s+[ivxlcdm\d]+|quyển\s+[ivxlcdm\d]+|quyển\s+(một|hai|ba|bốn|năm|sáu)|phần\s+[ivxlcdm\d]+|phụ lục\s+[a-z\d]+|chapter\s+\d+|part\s+\d+)\b", t, re.I):
        return True
    if re.match(r"^(đôi lời cùng bạn đọc|lời tựa|phi lộ|ghi chú về thư tịch|prologue|epilogue)\b", t, re.I) and len(t) < 60:
        return True
    if re.match(r"^(bản đồ|kết thúc|mở đầu|vĩ thanh|hết tập|hết quyển)$", t, re.I):
        return True
    return False


def is_heading(ln, body, left, is_toc_page=False):
    if is_toc_page:
        return False
    txt = ln.plain.strip()
    if not txt:
        return False

    # 1. Cỡ chữ lớn hơn đáng kể so với thân bài
    if ln.size >= body * 1.15 and len(txt) < 120:
        return True

    # 2. Khớp từ khóa chương / quyển / phần / phụ lục
    if is_major_chapter_heading(txt):
        return True

    # 3. Tiêu đề in hoa / in đậm ở giữa dòng, không có dấu câu kết thúc
    if len(txt) < 80 and not txt.endswith((".", ":", ";", ",", "!", "?", '"', "”")):
        if any(c in txt for c in ("!", "?", "”", '"')):
            return False
        if ln.bold and (txt.isupper() or ln.x0 > left + 30):
            return True
        if txt.isupper() and ln.x0 > left + 30 and len(txt) > 3:
            return True

    return False


def build_blocks(pages, body, left, right):
    """Trả về danh sách ('h', size, text, page) / ('p', html)."""
    toc_pages = find_toc_pages(pages)
    blocks = []
    cur = []        # các dòng của đoạn đang gom
    cur_last = None  # dòng cuối của đoạn đang gom
    head_buf = []   # tiêu đề nhiều dòng liên tiếp

    def flush_par():
        nonlocal cur, cur_last
        if cur:
            blocks.append(("p", " ".join(cur)))
        cur, cur_last = [], None

    def flush_head():
        nonlocal head_buf
        if head_buf:
            size = max(l.size for l in head_buf)
            text = " - ".join(l.plain for l in head_buf)
            blocks.append(("h", size, text, head_buf[0].page))
        head_buf = []

    indent_th = body * 0.6
    for pno, lines in enumerate(pages):
        is_toc = pno in toc_pages
        for ln in lines:
            if is_heading(ln, body, left, is_toc):
                flush_par()
                # Nếu tiêu đề trước đã có Chương/Quyển mà dòng mới lại bắt đầu Chương/Quyển -> flush trước
                if head_buf and (
                    abs(head_buf[-1].size - ln.size) > 0.6
                    or re.match(r"^(chương|quyển|phần)\b", ln.plain, re.I)
                ):
                    flush_head()
                head_buf.append(ln)
                continue
            flush_head()

            new_par = False
            if cur_last is None:
                new_par = True
            else:
                indented = ln.x0 - left > indent_th
                prev_short = cur_last.x1 < right - body * 1.5
                prev_ends = cur_last.plain.endswith(TERMINAL)
                dialog = ln.plain.startswith(DASHES) and prev_ends
                if indented or (prev_short and prev_ends) or dialog:
                    new_par = True
            if new_par:
                flush_par()
            cur.append(ln.html)
            cur_last = ln
    flush_par()
    flush_head()
    return blocks


def split_chapters(blocks, title, page_chunk=30):
    headings = [b for b in blocks if b[0] == "h"]
    if not headings:
        return None

    chapters = []  # (title, [html...])
    cur_title = title or "Mở đầu"
    cur_html = []

    for b in blocks:
        if b[0] == "h":
            h_text = b[2]
            # Nếu là tiêu đề chương chính và chương hiện tại đã có nội dung văn bản (<p>) -> tách chương
            has_paragraphs = any(x.startswith("<p>") for x in cur_html)
            if is_major_chapter_heading(h_text) and has_paragraphs:
                chapters.append((cur_title, cur_html))
                cur_title, cur_html = h_text, []
            else:
                if not has_paragraphs and cur_title != h_text:
                    cur_title = f"{cur_title} - {h_text}" if cur_title != (title or "Mở đầu") else h_text

            cur_html.append(f"<h2>{html.escape(h_text)}</h2>")
        else:
            cur_html.append(f"<p>{b[1]}</p>")

    if cur_html:
        chapters.append((cur_title, cur_html))
    return chapters


def chunk_chapters(blocks, title, per=60):
    """Không có tiêu đề: chia thành các phần đều nhau."""
    chapters, buf = [], []
    for b in blocks:
        buf.append(f"<p>{b[1]}</p>")
        if len(buf) >= per:
            chapters.append((f"Phần {len(chapters) + 1}", buf))
            buf = []
    if buf:
        chapters.append((f"Phần {len(chapters) + 1}", buf))
    return chapters


def write_epub(chapters, out, title, author, lang):
    book = epub.EpubBook()
    book.set_identifier(f"pdf2epub-{abs(hash(title))}")
    book.set_title(title)
    book.set_language(lang)
    book.add_author(author)
    css = epub.EpubItem(uid="css", file_name="style.css", media_type="text/css", content=CSS)
    book.add_item(css)

    items = []
    for i, (ctitle, parts) in enumerate(chapters, 1):
        c = epub.EpubHtml(title=ctitle[:100], file_name=f"ch{i:04d}.xhtml", lang=lang)
        c.content = "<html><head><link rel='stylesheet' href='style.css' type='text/css'/></head><body>" + "\n".join(parts) + "</body></html>"
        c.add_item(css)
        book.add_item(c)
        items.append(c)
    book.toc = items
    book.spine = ["nav"] + items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(out), book)


def main():
    ap = argparse.ArgumentParser(description="Chuyển PDF / EPUB sang EPUB/AZW3 sạch cho Kindle")
    ap.add_argument("input", help="Đường dẫn file .pdf hoặc .epub")
    ap.add_argument("-o", "--output")
    ap.add_argument("--title")
    ap.add_argument("--author", default="Unknown")
    ap.add_argument("--lang", default="vi")
    ap.add_argument("--strip", action="append", default=[],
                    help="regex bổ sung để xóa dòng rác (dùng nhiều lần được, áp dụng cho PDF)")
    ap.add_argument("--azw3", action="store_true", help="tạo thêm file AZW3 cho Kindle")
    ap.add_argument("--only-azw3", action="store_true", help="chỉ giữ file AZW3 duy nhất (tự xóa PDF/EPUB gốc và trung gian)")
    args = ap.parse_args()

    if args.only_azw3:
        args.azw3 = True

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"Không tìm thấy file: {src}")

    # Trường hợp 1: Đầu vào là EPUB -> chuyển thẳng sang AZW3
    if src.suffix.lower() == ".epub":
        exe = shutil.which("ebook-convert")
        if not exe and Path("/Applications/calibre.app/Contents/MacOS/ebook-convert").exists():
            exe = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
        if not exe:
            sys.exit("Không tìm thấy ebook-convert. Hãy cài Calibre và thêm vào PATH.")
        azw3 = Path(args.output) if args.output else src.with_suffix(".azw3")
        import os
        env = os.environ.copy()
        env.setdefault("CALIBRE_CONFIG_DIRECTORY", "/tmp/calibre_config")
        cmd = [exe, str(src), str(azw3)]
        if args.title:
            cmd += ["--title", args.title]
        if args.author != "Unknown":
            cmd += ["--authors", args.author]
        subprocess.run(cmd, env=env, check=True)
        print(f"Đã chuyển đổi EPUB -> AZW3 thành công: {azw3}")
        if args.only_azw3 and azw3.exists() and src != azw3:
            src.unlink()
            print(f"Đã xóa file gốc: {src}")
        return

    # Trường hợp 2: Đầu vào là PDF -> bóc tách làm sạch ra EPUB (+ AZW3 nếu có --azw3)
    out = Path(args.output) if args.output else src.with_suffix(".epub")
    doc = fitz.open(src)
    title = args.title or (doc.metadata or {}).get("title") or src.stem
    print(f"Đọc {len(doc)} trang...", file=sys.stderr)

    pages = extract_lines(doc)
    pages = remove_junk(pages, DEFAULT_JUNK + args.strip)
    body, left, right = analyze(pages)
    print(f"Cỡ chữ thân bài: {body}, lề trái: {left}, lề phải: {right}", file=sys.stderr)

    blocks = build_blocks(pages, body, left, right)
    chapters = split_chapters(blocks, title) or chunk_chapters(blocks, title)
    print(f"Tạo {len(chapters)} chương", file=sys.stderr)

    write_epub(chapters, out, title, args.author, args.lang)
    print(f"Đã lưu: {out}")

    if args.azw3:
        exe = shutil.which("ebook-convert")
        if not exe and Path("/Applications/calibre.app/Contents/MacOS/ebook-convert").exists():
            exe = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
        if not exe:
            sys.exit("Không tìm thấy ebook-convert. Hãy cài Calibre và thêm vào PATH.")
        azw3 = out.with_suffix(".azw3")
        import os
        env = os.environ.copy()
        env.setdefault("CALIBRE_CONFIG_DIRECTORY", "/tmp/calibre_config")
        subprocess.run([exe, str(out), str(azw3)], env=env, check=True)
        print(f"Đã lưu: {azw3}")
        if args.only_azw3 and azw3.exists():
            if out.exists() and out != azw3:
                out.unlink()
                print(f"Đã xóa file trung gian EPUB: {out}")
            if src.exists() and src != azw3:
                src.unlink()
                print(f"Đã xóa file gốc PDF: {src}")


if __name__ == "__main__":
    main()
