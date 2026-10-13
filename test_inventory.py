import json
import tempfile
import unittest
from pathlib import Path
from inventory_management import InventoryManager
class InventoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "data.json"
        source = Path(__file__).with_name("data.json")
        self.data_file.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        self.manager = InventoryManager(self.data_file)
    def tearDown(self):
        self.temp_dir.cleanup()
    def test_sale_reduces_stock(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction({
            "id": "TEST-SALE", "type": "sale", "branch": "B001",
            "product": "P001", "quantity": 2
        })
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before - 2)
    def test_sale_cannot_make_stock_negative(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction({
            "id": "TEST-TOO-MUCH", "type": "sale", "branch": "B001",
            "product": "P001", "quantity": 999
        })
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before)
    def test_duplicate_id_rejected(self):
        result = self.manager.process_transaction({
            "id": "T001", "type": "sale", "branch": "B001",
            "product": "P001", "quantity": 1
        })
        self.assertEqual(result["status"], "REJECTED")
    def test_transfer_updates_both_branches(self):
        source_before = self.manager.branches["B001"]["inventory"]["P001"]
        dest_before = self.manager.branches["B002"]["inventory"]["P001"]
        result = self.manager.process_transaction({
            "id": "TEST-TRANSFER", "type": "transfer", "from_branch": "B001",
            "to_branch": "B002", "product": "P001", "quantity": 3
        })
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], source_before - 3)
        self.assertEqual(self.manager.branches["B002"]["inventory"]["P001"], dest_before + 3)
    def test_same_branch_transfer_rejected(self):
        result = self.manager.process_transaction({
            "id": "TEST-SAME", "type": "transfer", "from_branch": "B001",
            "to_branch": "B001", "product": "P001", "quantity": 1
        })
        self.assertEqual(result["status"], "REJECTED")
    def test_invalid_product_rejected(self):
        result = self.manager.process_transaction({
            "id": "TEST-PRODUCT", "type": "sale", "branch": "B001",
            "product": "P999", "quantity": 1
        })
        self.assertEqual(result["status"], "REJECTED")
    def test_zero_quantity_rejected(self):
        result = self.manager.process_transaction({
            "id": "TEST-ZERO", "type": "purchase", "branch": "B001",
            "product": "P001", "quantity": 0
        })
        self.assertEqual(result["status"], "REJECTED")
    def test_purchase_adds_stock(self):
        before = self.manager.branches["B001"]["inventory"]["P001"]
        result = self.manager.process_transaction({
            "id": "TEST-PURCHASE", "type": "purchase", "branch": "B001",
            "product": "P001", "quantity": 4
        })
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.manager.branches["B001"]["inventory"]["P001"], before + 4)
    def test_save_and_reload(self):
        self.manager.process_transaction({
            "id": "TEST-SAVE", "type": "purchase", "branch": "B001",
            "product": "P001", "quantity": 1
        })
        self.manager.save_data()
        reloaded = InventoryManager(self.data_file)
        self.assertTrue(any(t["id"] == "TEST-SAVE" for t in reloaded.transactions))
if __name__ == "__main__":
    unittest.main()
