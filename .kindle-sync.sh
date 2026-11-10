#!/bin/zsh
# Script tu dong kéo sach tu Git (https://github.com/ntg552000/books.git)
# va chep vao Kindle Paperwhite 1 moi khi cam cap USB vao may Mac.

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Applications/calibre.app/Contents/MacOS"

KINDLE_MOUNT="/Volumes/Kindle"
KINDLE_DOCS="${KINDLE_MOUNT}/documents"
REPO_DIR="/Users/ntg552000/KindleBooks"
LOG_FILE="${REPO_DIR}/.sync.log"
LOCK_DIR="/tmp/kindle_git_sync.lock"

# 1. Kiem tra Kindle co dang duoc cam USB khong
if [ ! -d "$KINDLE_DOCS" ]; then
    exit 0
fi

# 2. Chống chạy trùng lặp khi macOS kích hoạt sự kiện mount nhiều lần
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

echo "==================================================" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Phat hien Kindle tai $KINDLE_MOUNT" >> "$LOG_FILE"

notify_mac() {
    local msg="$1"
    osascript -e "display notification \"$msg\" with title \"Kindle Git Sync\"" >/dev/null 2>&1 || true
}

notify_mac "Đang kiểm tra sách mới từ GitHub (ntg552000/books)..."

# 3. Kéo sách mới nhất từ GitHub về máy Mac
if [ -d "${REPO_DIR}/.git" ]; then
    cd "$REPO_DIR" || exit 1
    git fetch --all >> "$LOG_FILE" 2>&1
    # Nếu repo trên remote đã có nhánh thì pull về
    if git rev-parse --verify origin/main >/dev/null 2>&1; then
        git pull --rebase --autostash origin main >> "$LOG_FILE" 2>&1 || true
    elif git rev-parse --verify origin/master >/dev/null 2>&1; then
        git pull --rebase --autostash origin master >> "$LOG_FILE" 2>&1 || true
    else
        git pull --rebase --autostash >> "$LOG_FILE" 2>&1 || true
    fi
fi

# 4. Nếu có file .epub và máy Mac có cài Calibre (ebook-convert), tự động chuyển sang .azw3
if command -v ebook-convert >/dev/null 2>&1; then
    while IFS= read -r -d '' epub_file; do
        azw3_file="${epub_file%.*}.azw3"
        if [ ! -f "$azw3_file" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Chuyen doi $(basename "$epub_file") -> .azw3" >> "$LOG_FILE"
            ebook-convert "$epub_file" "$azw3_file" >> "$LOG_FILE" 2>&1 || true
        fi
    done < <(find "$REPO_DIR" -maxdepth 3 -not -path '*/.*' -type f -iname "*.epub" -print0)
fi

# 5. Đồng bộ các file sách (.azw3, .mobi, .pdf, .prc, .txt, .azw) vào /Volumes/Kindle/documents
NEW_COUNT=0
TOTAL_COUNT=0

while IFS= read -r -d '' book_file; do
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    book_name=$(basename "$book_file")
    dest_file="${KINDLE_DOCS}/${book_name}"

    # Chỉ copy nếu file chưa có trên Kindle hoặc dung lượng thay đổi
    if [ ! -f "$dest_file" ] || [ "$(stat -f%z "$book_file")" != "$(stat -f%z "$dest_file")" ]; then
        cp -X "$book_file" "$dest_file"
        NEW_COUNT=$((NEW_COUNT + 1))
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] + Da chep sach moi: $book_name" >> "$LOG_FILE"
    fi
done < <(find "$REPO_DIR" -maxdepth 3 -not -path '*/.*' -type f \( -iname "*.azw3" -o -iname "*.mobi" -o -iname "*.pdf" -o -iname "*.prc" -o -iname "*.txt" -o -iname "*.azw" \) -print0)

# 6. Dọn các file rác ._* của macOS trên Kindle để không làm rối thư viện Kindle
dot_clean -m "$KINDLE_MOUNT" >/dev/null 2>&1 || true
find "$KINDLE_DOCS" -maxdepth 2 -name "._*" -delete >/dev/null 2>&1 || true

# 7. Thông báo kết quả
if [ "$NEW_COUNT" -gt 0 ]; then
    notify_mac "Đã tải và chép ${NEW_COUNT} cuốn sách mới vào Kindle!"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Hoan tat: Da chep $NEW_COUNT sach moi (Tong: $TOTAL_COUNT)." >> "$LOG_FILE"
else
    notify_mac "Kindle đã có đủ sách mới nhất từ GitHub (${TOTAL_COUNT} cuốn)."
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Hoan tat: Khong co sach moi can chep (Tong: $TOTAL_COUNT)." >> "$LOG_FILE"
fi
