---
title: Book Logistics Platform — Product Card
doc_id: CARD-book-logistics-platform
doc_type: product-card
verdict: template
version: 0.1
date: 2026-10-08
status: ร่างเพื่อพิจารณา
owner: Product owner (Book Dev)
---

# Book Logistics Platform — Product Card

## 1. สรุปคุณค่าในหนึ่งประโยค

ขอบเขต repository สำหรับความสามารถ `logistics` ของ Book Platform ที่มีเพียง contract และการตรวจรูปแบบ ยังไม่มี runtime ให้ผู้ใช้คนใด

## 2. ปัญหาและผู้ใช้เป้าหมาย

- ปัญหาที่ตั้งใจแก้: แยกความสามารถ `logistics` (carrier adapters, shipment, shipping rates, labels, tracking) ออกจาก monorepo `solo-empire` เป็น platform ที่สื่อสารผ่าน versioned contract
- ผู้ใช้เป้าหมาย: เจ้าของในบทบาท platform engineer และผลิตภัณฑ์ภายในที่จะเรียกใช้ในอนาคต **[สมมติฐาน]**

## 3. สิ่งที่ทำได้จริงตอนนี้

- `contract.json` (`book-platform.contract.v1`, status `scaffolded`) และ schema ที่ vendored พร้อม sha256 pin
- `scripts/check.py` ตรวจ contract กับ schema และความสอดคล้องของ README กับ `docs/CONTRACT-SURFACE.md`; `check_schema_pin.py` และ `check_registry_alignment.py` ตรวจ drift
- test 2 ไฟล์ (`test_contract_check.py`, `test_drift_checks.py`) ตรวจเฉพาะ contract/drift
- ไม่มีโค้ด runtime สำหรับ carrier adapters, shipment, shipping rates, labels, tracking; implementation ปัจจุบันตาม registry คือ ไม่มี (`none`) — ยังไม่มีที่ใดทำความสามารถนี้

## 4. หลักฐานสถานะ

- Stack: Python 3.11 standard library (`scripts/check.py`, `unittest`), GitHub Actions
- Commits: 5 (แรก 2026-09-17 / ล่าสุด 2026-10-01), branch `main`
- Source: 5 ไฟล์ / 828 บรรทัด; test files: 2
- CI workflows: 1; deploy config: ไม่มี
- Live URL: ไม่มี
- README: 65 บรรทัด ("book-logistics-platform"); template family size: 10

## 5. คุณค่าและจุดต่าง

- คุณค่าอยู่ที่วินัยของ contract (dependency ชัด: `identity`, `observability`; migration gate ที่ต้องมี parity และ rollback ก่อน cutover)
- **[สมมติฐาน]** ไม่มีมุมรายได้; เป็นโครงสำหรับแยก platform ในอนาคต

## 6. ช่องว่างสำคัญ

- ไม่มี runtime, API หรือข้อมูล — README ระบุเองว่าไม่อ้าง deployment หรือ production readiness
- เป็นหนึ่งใน template family ขนาด 10 ที่โครงเหมือนกันทุกไฟล์
- ยังไม่มีเหตุผลทางธุรกิจที่ต้องแยก repo ตอนนี้ **[ต้องยืนยัน]**

## 7. คำตัดสิน

**template** — scaffold ของ platform boundary ที่ไม่มีความสามารถจริง ห้ามอ้างเป็นผลิตภัณฑ์ เงื่อนไขที่จะเปลี่ยนคำตัดสิน: มี consumer จริงอย่างน้อยหนึ่งรายและย้าย implementation มาพร้อมหลักฐาน shadow parity จึงประเมินเป็น `invest`

## 8. ขั้นต่อไป

1. คงสถานะ `scaffolded` และไม่เพิ่มโค้ดจนกว่าจะมี consumer ที่อนุมัติ
2. พิจารณารวม scaffold ที่ไม่มีแผนใกล้ ๆ ไว้ใน `bookchaowalit-backend-core` เพื่อลดจำนวน repo

## การอนุมัติ

| บทบาท | การตัดสินใจ | วันที่ |
|---|---|---|
| Product owner (Book Dev) | [ต้องยืนยัน] verdict `template` | — |

## แหล่งข้อมูล

- `bookchaowalit-backend/book-backend/book-logistics-platform/README.md`
- `bookchaowalit-backend/book-backend/book-logistics-platform/contract.json`
- `bookchaowalit-backend/book-backend/book-logistics-platform/scripts/check.py`
- `repository-catalog/roadmaps/platforms-backend.yaml`
