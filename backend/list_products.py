"""Print the products in the store (read-only)."""
import store

SHOW = {"SALE"}  # statuses to print; add "OUTOFSTOCK" etc. if needed

snap = store.snapshot(max_age=0)
if "error" in snap:
    print("Request failed:", snap["error"])
    raise SystemExit(1)

print("Total products:", len(snap["products"]), "| by status:", snap["count_by_status"])
for p in snap["products"]:
    if p["status"] in SHOW:
        print(p["no"], p["status"], p["price"], p["stock"], p["name"])
