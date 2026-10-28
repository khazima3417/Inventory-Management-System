"""Tests for InventoryManager."""
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from inventory_management import InventoryManager, InventoryError
class InventoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "data.json"
        source = Path(__file__).with_name("data.json")
        self.data_file.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        self.manager = InventoryManager(self.data_file)
    def tearDown(self):
        self.temp_dir.cleanup()
    def transaction(self, transaction_id="TEST-1", kind="sale", branch="B001", product="P001", quantity=1, **extra):
        item = {"id": transaction_id, "type": kind, "branch": branch,
                "product": product, "quantity": quantity}
        item.update(extra)
        return item
    def test_sale_reduces_stock(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction(self.transaction(quantity=2))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before - 2)
    def test_sale_cannot_make_stock_negative(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction(self.transaction("TEST-TOO-MUCH", quantity=999))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before)
        self.assertIn("Available", result["reason"])
        self.assertIn("requested", result["reason"])
    def test_duplicate_id_rejected_without_adding_duplicate(self):
        before_count = len(self.manager.transactions)
        result = self.manager.process_transaction(self.transaction("T001"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("Duplicate", result["reason"])
        self.assertEqual(len(self.manager.transactions), before_count)
    def test_transfer_updates_both_branches(self):
        source_before = self.manager.branches["B001"]["inventory"]["P001"]
        dest_before = self.manager.branches["B002"]["inventory"].get("P001", 0)
        result = self.manager.process_transaction({
            "id": "TEST-TRANSFER", "type": "transfer", "from_branch": "B001",
            "to_branch": "B002", "product": "P001", "quantity": 3,
        })
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], source_before - 3)
        self.assertEqual(self.manager.branches["B002"]["inventory"]["P001"], dest_before + 3)
    def test_same_branch_transfer_rejected(self):
        result = self.manager.process_transaction({
            "id": "TEST-SAME", "type": "transfer", "from_branch": "B001",
            "to_branch": "B001", "product": "P001", "quantity": 1,
        })
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("same", result["reason"])
    def test_transfer_insufficient_stock_does_not_change_either_branch(self):
        source_before = self.manager.branches["B001"]["inventory"]["P001"]
        dest_before = self.manager.branches["B002"]["inventory"].get("P001", 0)
        result = self.manager.process_transaction({
            "id": "TEST-TRANSFER-MORE", "type": "transfer", "from_branch": "B001",
            "to_branch": "B002", "product": "P001", "quantity": 999,
        })
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], source_before)
        self.assertEqual(self.manager.branches["B002"]["inventory"].get("P001", 0), dest_before)
    def test_invalid_product_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-PRODUCT", product="P999"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("product", result["reason"].lower())
    def test_invalid_branch_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-BRANCH", branch="B999"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("branch", result["reason"].lower())
    def test_invalid_transaction_type_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-TYPE", kind="adjustment"))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("type", result["reason"].lower())
    def test_zero_quantity_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-ZERO", kind="purchase", quantity=0))
        self.assertEqual(result["status"], "REJECTED")
    def test_negative_quantity_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-NEGATIVE", quantity=-2))
        self.assertEqual(result["status"], "REJECTED")
    def test_fractional_quantity_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-FRACTION", quantity=1.5))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("whole number", result["reason"])
    def test_missing_transaction_fields_rejected(self):
        result = self.manager.process_transaction({"id": "TEST-MISSING", "type": "sale"})
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("Missing field", result["reason"])
    def test_purchase_adds_stock(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction(self.transaction(
            "TEST-PURCHASE", kind="purchase", quantity=4, expiry_date="2027-12-31"
        ))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before + 4)
    def test_supplier_return_cannot_exceed_stock(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction(self.transaction("TEST-SUPPLIER", kind="supplier_return", quantity=999))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before)
    def test_customer_return_cannot_exceed_original_sale(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction(self.transaction("TEST-RETURN", kind="customer_return", quantity=6))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before)
    def test_customer_return_within_original_sale_is_accepted(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction(self.transaction(
            "TEST-RETURN-OK", kind="customer_return", quantity=2, expiry_date="2027-12-31"
        ))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before + 2)
    def test_save_and_reload(self):
        self.manager.process_transaction(self.transaction(
            "TEST-SAVE", kind="purchase", quantity=1, expiry_date="2027-12-31"
        ))
        self.manager.save_data()
        reloaded = InventoryManager(self.data_file)
        self.assertTrue(any(t.get("id") == "TEST-SAVE" for t in reloaded.transactions))
        self.assertEqual(reloaded.branches["B001"]["inventory"]["P001"], 41)
    def test_empty_sales_analytics_does_not_crash(self):
        self.manager.transactions = []
        with redirect_stdout(StringIO()):
            report = self.manager.sales_analytics()
        self.assertEqual(report["revenue"], 0)
        self.assertEqual(report["units"], 0)
        self.assertIsNone(report["best_product"])
        self.assertIsNone(report["worst_product"])

    def test_empty_batches_report_does_not_crash(self):
        self.manager.batches = []
        with redirect_stdout(StringIO()):
            report = self.manager.product_expiry()
        self.assertEqual(report, [])

    def test_unknown_batch_is_reported_without_crash(self):
        self.manager.batches = [{"batch": "BAT-UNKNOWN", "product": "P999", "branch": "B001", "expiry_days": 3}]
        with redirect_stdout(StringIO()):
            report = self.manager.product_expiry()
        self.assertIn("unknown product or branch", report[0]["status"])

    def test_duplicate_batch_id_is_reported(self):
        self.manager.batches = [
            {"batch": "DUP", "product": "P001", "branch": "B001", "expiry_days": 5},
            {"batch": "DUP", "product": "P002", "branch": "B002", "expiry_days": 10},
        ]
        with redirect_stdout(StringIO()):
            report = self.manager.product_expiry()
        self.assertIn("duplicate batch ID", report[1]["status"])
    def test_missing_file_raises_clear_inventory_error(self):
        missing = Path(self.temp_dir.name) / "missing.json"
        with self.assertRaises(InventoryError):
            InventoryManager(missing)
    def test_invalid_json_raises_clear_inventory_error(self):
        self.data_file.write_text("{invalid json", encoding="utf-8")
        with self.assertRaises(InventoryError):
            InventoryManager(self.data_file)
    def test_non_dictionary_transaction_is_rejected_without_crash(self):
        result = self.manager.process_transaction(["not", "a", "dictionary"])
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("dictionary", result["reason"].lower())
    def test_nan_quantity_is_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-NAN", quantity=float("nan")))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], 40)
    def test_boolean_quantity_is_rejected(self):
        result = self.manager.process_transaction(self.transaction("TEST-BOOL", quantity=True))
        self.assertEqual(result["status"], "REJECTED")
    def test_reconciliation_ignores_invalid_nan_stock(self):
        with redirect_stdout(StringIO()):
            report = self.manager.reconcile_inventory({"B001": {"P001": float("nan")}})
        self.assertEqual(report, {"MATCHED": 0, "SHORTAGE": 0, "EXCESS": 0})
    def test_dashboard_and_reports_work_with_empty_transactions(self):
        self.manager.transactions = []
        with redirect_stdout(StringIO()):
            self.manager.dashboard()

    def test_nan_rejection_can_be_saved_without_corrupting_json(self):
        self.manager.process_transaction(self.transaction("TEST-NAN-SAVE", quantity=float("nan")))
        self.manager.save_data()
        with self.data_file.open("r", encoding="utf-8") as file:
            saved = json.load(file)
        rejected = next(item for item in saved["transactions"] if item.get("id") == "TEST-NAN-SAVE")
        self.assertEqual(rejected["quantity"], "nan")

    def test_rejected_transaction_id_can_be_reused(self):
        first = self.manager.process_transaction(self.transaction("RETRY-ME", quantity=999))
        second = self.manager.process_transaction(self.transaction("RETRY-ME", quantity=1))
        self.assertEqual(first["status"], "REJECTED")
        self.assertEqual(second["status"], "SUCCESS")

    def test_customer_returns_reduce_net_sales_analytics(self):
        self.manager.transactions = []
        with redirect_stdout(StringIO()):
            self.manager.process_transaction(self.transaction("RETURN-SALE", quantity=5))
            self.manager.process_transaction(self.transaction(
                "RETURN-BACK", kind="customer_return", quantity=5, expiry_date="2027-12-31"
            ))
            report = self.manager.sales_analytics()
        self.assertEqual(report["revenue"], 0)
        self.assertEqual(report["units"], 0)

    def test_historical_sales_keep_transaction_time_price(self):
        self.manager.transactions = []
        self.manager.process_transaction(self.transaction("PRICE-SALE", quantity=2))
        self.manager.products["P001"]["price"] = 999
        with redirect_stdout(StringIO()):
            report = self.manager.sales_analytics()
        self.assertEqual(report["revenue"], 200)

    def test_whole_number_float_quantity_is_stored_as_integer(self):
        result = self.manager.process_transaction(self.transaction(
            "WHOLE-FLOAT", kind="purchase", quantity=5.0, expiry_date="2027-12-31"
        ))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIsInstance(result["quantity"], int)

    def test_rejected_id_can_be_reused_after_save_and_reload(self):
        first = self.manager.process_transaction(self.transaction("RETRY", quantity=999))
        second = self.manager.process_transaction(self.transaction("RETRY", quantity=1))
        self.assertEqual(first["status"], "REJECTED")
        self.assertEqual(second["status"], "SUCCESS")
        self.manager.save_data()
        reloaded = InventoryManager(self.data_file)
        self.assertEqual(sum(t.get("id") == "RETRY" for t in reloaded.transactions), 2)

    def test_repeated_rejected_id_can_be_saved_and_reloaded(self):
        self.manager.process_transaction(self.transaction("RETRY", quantity=999))
        self.manager.process_transaction(self.transaction("RETRY", quantity=999))
        self.manager.save_data()
        reloaded = InventoryManager(self.data_file)
        self.assertEqual(sum(t.get("id") == "RETRY" for t in reloaded.transactions), 2)

    def test_expired_batch_is_not_consumed_for_sale(self):
        self.manager.batches = [
            {"batch": "OLD", "product": "P001", "branch": "B001", "quantity": 20,
             "expiry_date": "2020-01-01"},
            {"batch": "NEW", "product": "P001", "branch": "B001", "quantity": 20,
             "expiry_date": "2099-01-01"},
        ]
        result = self.manager.process_transaction(self.transaction("EXPIRY-SALE", quantity=5))
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.batches[0]["quantity"], 20)
        self.assertEqual(self.manager.batches[1]["quantity"], 15)

    def test_sale_rejected_when_unexpired_batches_are_insufficient(self):
        self.manager.batches = [
            {"batch": "OLD", "product": "P001", "branch": "B001", "quantity": 20,
             "expiry_date": "2020-01-01"},
        ]
        result = self.manager.process_transaction(self.transaction("EXPIRED-ONLY", quantity=1))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], 40)

    def test_badly_typed_batch_expiry_is_rejected_on_load(self):
        raw = json.loads(self.data_file.read_text(encoding="utf-8"))
        raw["batches"][0]["expiry_date"] = 20250101
        self.data_file.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(InventoryError, "expiry_date must be text"):
            InventoryManager(self.data_file)

    def test_process_transaction_does_not_mutate_input(self):
        transaction = self.transaction("COPY-INPUT", kind="purchase", quantity=5.0,
                                       expiry_date="2027-12-31")
        original = dict(transaction)
        self.manager.process_transaction(transaction)
        self.assertEqual(transaction, original)

    def test_purchase_creates_expiry_batch(self):
        result = self.manager.process_transaction(self.transaction(
            "PURCHASE-BATCH", kind="purchase", quantity=3, expiry_date="2027-12-31"
        ))
        self.assertEqual(result["status"], "SUCCESS")
        batch = next(b for b in self.manager.batches if b.get("batch") == "BATCH-PURCHASE-BATCH")
        self.assertEqual(batch["quantity"], 3)
        self.assertEqual(batch["expiry_date"], "2027-12-31")

if __name__ == "__main__":
    unittest.main(verbosity=2)
