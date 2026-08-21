"""One-shot bulk loader: Excel → Mongo products, image folder → R2.

Reuses the exact same import pipeline as POST /products/bulk-upload
(categories/subcategories auto-created, rows sharing a product name
merge into variants, opening stock seeds inventory), then walks an
image folder and attaches each image to its product by filename.

Usage:
    cd backend
    uv run python -m scripts.bulk_load \
        --mongo-uri "mongodb+srv://user:pass@cluster/..." \
        --db-name petshop \
        --excel /path/to/products.xlsx \
        --images-dir /path/to/images \
        --r2-endpoint https://<account>.r2.cloudflarestorage.com \
        --r2-access-key KEY \
        --r2-secret-key SECRET \
        --r2-bucket pet-shop-products \
        --r2-public-url https://pub-xxxx.r2.dev

Every --r2-* / --mongo-* flag falls back to the matching env var
(MONGO_URI, DB_NAME, R2_ENDPOINT_URL, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_PUBLIC_BASE_URL), so if
backend/.env is already filled in you only need --excel and
--images-dir.

Image matching (case-insensitive; spaces/dashes/underscores ignored):
    <filename stem>            is matched against, in order:
      1. a variant SKU         → image is set ON THAT VARIANT
      2. client_product_code   → appended to product images[]
      3. product code PRD-XXXX → appended to product images[]
      4. product name          → appended to product images[]
    Multi-image: name_1.jpg, name-2.png etc. — the trailing _N/-N is
    stripped before matching, so all of them land on the same product.

Idempotency: products that already have images are SKIPPED unless
--force is passed. Use --dry-run to preview the matching without
writing anything (no Mongo insert, no R2 upload).
"""
import argparse
import os
import re
import sys
from pathlib import Path


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _parse_args():
    p = argparse.ArgumentParser(
        description="Bulk-load products from Excel into Mongo and images into R2.",
    )
    p.add_argument("--excel", required=True, help="Path to the .xlsx workbook")
    p.add_argument("--images-dir", help="Folder of product images (optional)")
    p.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI"),
                   help="Mongo connection string (default: $MONGO_URI)")
    p.add_argument("--db-name", default=os.environ.get("DB_NAME", "petshop"),
                   help="Database name (default: $DB_NAME or 'petshop')")
    p.add_argument("--r2-endpoint", default=os.environ.get("R2_ENDPOINT_URL"))
    p.add_argument("--r2-access-key", default=os.environ.get("R2_ACCESS_KEY_ID"))
    p.add_argument("--r2-secret-key", default=os.environ.get("R2_SECRET_ACCESS_KEY"))
    p.add_argument("--r2-bucket", default=os.environ.get("R2_BUCKET"))
    p.add_argument("--r2-public-url", default=os.environ.get("R2_PUBLIC_BASE_URL"))
    p.add_argument("--force", action="store_true",
                   help="Upload images even if the product already has some (appends)")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse + match only; no Mongo writes, no R2 uploads")
    p.add_argument("--skip-excel", action="store_true",
                   help="Skip the Excel import; only match + upload images against existing products")
    return p.parse_args()


def _normalize(s):
    """Lowercase and drop spaces/dashes/underscores/dots so 'PED-CHK 1k'
    matches 'ped_chk1K'."""
    return re.sub(r"[\s\-_.]+", "", (s or "").lower())


def _stem_for_match(path):
    """Filename stem with any trailing _N / -N multi-image suffix removed."""
    stem = path.stem
    return re.sub(r"[\s\-_](\d{1,2})$", "", stem)


def main():
    args = _parse_args()

    # Env must be set BEFORE importing config-backed modules.
    if args.mongo_uri:
        os.environ["MONGO_URI"] = args.mongo_uri
    os.environ["DB_NAME"] = args.db_name
    if args.r2_endpoint:
        os.environ["R2_ENDPOINT_URL"] = args.r2_endpoint
    if args.r2_access_key:
        os.environ["R2_ACCESS_KEY_ID"] = args.r2_access_key
    if args.r2_secret_key:
        os.environ["R2_SECRET_ACCESS_KEY"] = args.r2_secret_key
    if args.r2_bucket:
        os.environ["R2_BUCKET"] = args.r2_bucket
    if args.r2_public_url:
        os.environ["R2_PUBLIC_BASE_URL"] = args.r2_public_url

    excel_path = Path(args.excel)
    if not excel_path.is_file():
        sys.exit(f"Excel file not found: {excel_path}")
    images_dir = Path(args.images_dir) if args.images_dir else None
    if images_dir and not images_dir.is_dir():
        sys.exit(f"Images folder not found: {images_dir}")

    from config.db import mongo_manager, get_db
    mongo_manager.connect()
    db = get_db()
    print(f"[mongo] connected: db={db.name}")

    # ---------- 1. Excel import ----------
    if args.skip_excel:
        print("[excel] skipped (--skip-excel)")
    elif args.dry_run:
        from services.product_service import parse_products_workbook
        rows, header_error = parse_products_workbook(excel_path.read_bytes())
        if header_error:
            sys.exit(f"[excel] header error: {header_error}")
        names = {r["name"] for r in rows if r.get("name")}
        print(f"[excel] DRY RUN — {len(rows)} rows, {len(names)} distinct products. "
              f"Nothing written.")
    else:
        from services.product_service import import_products
        summary = import_products(excel_path.read_bytes())
        print(f"[excel] created={summary['created']} updated={summary['updated']} "
              f"failed={summary['failed']} warnings={summary.get('warnings', 0)}")
        if summary["categories_created"]:
            print(f"[excel] categories auto-created: {summary['categories_created']}")
        if summary["subcategories_created"]:
            print(f"[excel] subcategories auto-created: {summary['subcategories_created']}")
        for r in summary["rows"]:
            if r.get("error"):
                print(f"[excel]   row {r['row']}: {r['action']} — {r['error']}")
            elif r.get("warning"):
                print(f"[excel]   row {r['row']}: WARNING — {r['warning']}")

    if not images_dir:
        print("[images] no --images-dir given; done.")
        return

    # ---------- 2. Build the match index from Mongo ----------
    from config.config import settings
    products_coll = db[settings.PRODUCTS_COLL]

    by_key = {}          # normalized identifier -> product doc
    by_sku = {}          # normalized sku -> (product doc, variant _id)
    for doc in products_coll.find({}):
        for field in ("client_product_code", "code", "name"):
            k = _normalize(doc.get(field))
            if k:
                by_key.setdefault(k, doc)
        for v in doc.get("variants") or []:
            k = _normalize(v.get("sku"))
            if k:
                by_sku.setdefault(k, (doc, v["_id"]))

    # Dry-run never wrote the Excel to Mongo, so also index the products
    # the workbook WOULD create — otherwise the preview reports every
    # image as unmatched on a fresh DB.
    if args.dry_run and not args.skip_excel:
        from services.product_service import parse_products_workbook
        rows, _err = parse_products_workbook(excel_path.read_bytes())
        for r in rows or []:
            phantom = {"_id": None, "name": r.get("name"),
                       "client_product_code": r.get("client_product_code"),
                       "code": None, "images": []}
            for field in ("client_product_code", "name"):
                k = _normalize(r.get(field))
                if k:
                    by_key.setdefault(k, phantom)
            k = _normalize(r.get("variant_sku"))
            if k:
                by_sku.setdefault(k, (phantom, None))

    files = sorted(
        f for f in images_dir.iterdir()
        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
    )
    print(f"[images] {len(files)} image file(s) in {images_dir}")

    from bson import ObjectId
    from helpers.datetime import now_utc
    if not args.dry_run:
        from utils import r2_storage

    def _upload_or_exit(doc, f):
        """Upload one file to R2 under the product's prefix; exits the
        script with a clear message if R2 creds are missing."""
        try:
            return r2_storage.upload_image(
                key_prefix=f"products/{doc.get('code') or doc['_id']}",
                data=f.read_bytes(),
                content_type=_CONTENT_TYPES[f.suffix.lower()],
                filename=f.name,
            )
        except r2_storage.R2NotConfiguredError as e:
            sys.exit(
                f"\n[error] {e}\nPass --r2-endpoint / --r2-access-key / "
                f"--r2-secret-key / --r2-bucket / --r2-public-url or fill "
                f"them into backend/.env. (Products were already imported — "
                f"re-run with --skip-excel to only do images.)"
            )

    def _is_url(s):
        return bool(s) and (s.startswith("http://") or s.startswith("https://"))

    # ---------- 2b. Resolve sheet-referenced filenames → R2 links ----------
    # The Excel's Images / Variant Image columns may contain bare
    # filenames (e.g. "PED-AD.png") instead of URLs. The import stores
    # them verbatim, so here we find each referenced file in the images
    # folder, upload it, and REPLACE the filename with the R2 link.
    files_by_name = {f.name.lower(): f for f in files}
    consumed = set()   # files used by the resolve phase — phase 3 skips them
    resolved = missing_refs = 0
    if not (args.dry_run and not args.skip_excel):
        for doc in products_coll.find({}):
            imgs = doc.get("images") or []
            if any(not _is_url(i) for i in imgs):
                new_list = []
                changed = False
                for entry in imgs:
                    if _is_url(entry):
                        new_list.append(entry)
                        continue
                    f = files_by_name.get((entry or "").strip().lower())
                    if not f:
                        print(f"[resolve]   ✗ '{doc['name']}': sheet image "
                              f"'{entry}' not found in {images_dir}")
                        missing_refs += 1
                        new_list.append(entry)
                        continue
                    consumed.add(f.name)
                    if args.dry_run:
                        print(f"[resolve]   ✓ '{doc['name']}': {entry} → R2 (dry run)")
                        resolved += 1
                        new_list.append(entry)
                        continue
                    result = _upload_or_exit(doc, f)
                    new_list.append(result["url"])
                    changed = True
                    resolved += 1
                    print(f"[resolve]   ✓ '{doc['name']}': {entry} → {result['url']}")
                if changed:
                    products_coll.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"images": new_list, "updated_at": now_utc()}},
                    )
            for v in doc.get("variants") or []:
                entry = v.get("image")
                if not entry or _is_url(entry):
                    continue
                f = files_by_name.get(entry.strip().lower())
                if not f:
                    print(f"[resolve]   ✗ '{doc['name']}' variant: sheet image "
                          f"'{entry}' not found in {images_dir}")
                    missing_refs += 1
                    continue
                consumed.add(f.name)
                if args.dry_run:
                    print(f"[resolve]   ✓ '{doc['name']}' variant: {entry} → R2 (dry run)")
                    resolved += 1
                    continue
                result = _upload_or_exit(doc, f)
                products_coll.update_one(
                    {"_id": doc["_id"], "variants._id": v["_id"]},
                    {"$set": {"variants.$.image": result["url"],
                              "updated_at": now_utc()}},
                )
                resolved += 1
                print(f"[resolve]   ✓ '{doc['name']}' variant: {entry} → {result['url']}")
        if resolved or missing_refs:
            print(f"[resolve] replaced {resolved} sheet filename(s) with R2 links; "
                  f"{missing_refs} referenced file(s) missing from the folder")

    # ---------- 3. Match remaining folder files by filename ----------
    uploaded = skipped = unmatched = 0
    for f in files:
        if f.name in consumed:
            print(f"[images]   - {f.name}: already uploaded via sheet reference")
            skipped += 1
            continue
        key = _normalize(_stem_for_match(f))
        target_variant = by_sku.get(key)
        target_product = by_key.get(key)

        if target_variant:
            doc, variant_id = target_variant
            label = f"variant sku on '{doc['name']}'"
        elif target_product:
            doc, variant_id = target_product, None
            label = f"product '{doc['name']}'"
        else:
            print(f"[images]   ✗ {f.name}: no product/SKU match")
            unmatched += 1
            continue

        if not args.force and variant_id is None and doc.get("images"):
            print(f"[images]   - {f.name}: {label} already has images (use --force)")
            skipped += 1
            continue

        if args.dry_run:
            print(f"[images]   ✓ {f.name} → {label} (dry run)")
            uploaded += 1
            continue

        result = _upload_or_exit(doc, f)
        if variant_id is not None:
            products_coll.update_one(
                {"_id": doc["_id"], "variants._id": variant_id},
                {"$set": {"variants.$.image": result["url"],
                          "updated_at": now_utc()}},
            )
        else:
            products_coll.update_one(
                {"_id": doc["_id"]},
                {"$push": {"images": result["url"]},
                 "$set": {"updated_at": now_utc()}},
            )
        print(f"[images]   ✓ {f.name} → {label} → {result['url']}")
        uploaded += 1

    print(f"[done] uploaded={uploaded} skipped={skipped} unmatched={unmatched}")


if __name__ == "__main__":
    main()
