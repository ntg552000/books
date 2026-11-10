# 📚 Kho Sách Kindle (`ntg552000/books`)

Kho lưu trữ sách tự động đồng bộ với **Kindle Paperwhite 1**.

## Cách hoạt động
1. Thêm file sách (`.azw3`, `.mobi`, `.pdf`, `.prc`, `.txt`) vào repository này (có thể upload trực tiếp trên web GitHub hoặc `git push` từ máy tính).
2. Mỗi khi cắm cáp USB nối Kindle vào máy Mac:
   - Dịch vụ ngầm (`com.ntg552000.kindle-git-sync`) sẽ tự động chạy `git pull` tại `~/KindleBooks`.
   - Tự động chép toàn bộ sách mới vào `/Volumes/Kindle/documents/`.
   - Hiện thông báo hoàn tất trên góc màn hình Mac.

## Chạy đồng bộ thủ công bất kỳ lúc nào
```bash
~/KindleBooks/.kindle-sync.sh
```
