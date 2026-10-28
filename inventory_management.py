import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
class InventoryError(Exception):
    """Base error for inventory operations."""
class InventoryManager:
    """Manage products, branch stock, transactions, batches, and reports."""

    @staticmethod
    def _is_whole_number(value):
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and value >= 0 and float(value).is_integer())

    @staticmethod
    def _json_safe(value):
        """Convert rejected raw input into JSON-safe values without losing the reason."""
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else repr(value)
        if isinstance(value, dict):
            return {str(k): InventoryManager._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [InventoryManager._json_safe(v) for v in value]
        if isinstance(value, (set, frozenset)):
            return repr(value)
        return repr(value)
    VALID_TYPES = {"sale", "purchase", "customer_return", "supplier_return", "transfer"}
    def __init__(self, data_file=None):
        self.data_file = Path(data_file) if data_file is not None else Path(__file__).with_name("data.json")
        self.data = self.load_data()
        self.products = self.data["products"]
        self.branches = self.data["branches"]
        self.transactions = self.data["transactions"]
        self.batches = self.data["batches"]
        self.physical_stock = self.data.get("physical_stock", {})
        self.data["physical_stock"] = self.physical_stock
    def load_data(self):
        try:
            with self.data_file.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError as error:
            raise InventoryError(f"Data file not found: {self.data_file}") from error
        except json.JSONDecodeError as error:
            raise InventoryError(f"Invalid JSON in {self.data_file}: {error}") from error
        except (OSError, UnicodeDecodeError) as error:
            raise InventoryError(f"Could not read {self.data_file}: {error}") from error
        if not isinstance(data, dict):
            raise InventoryError("The JSON root must be a dictionary/object.")
        expected = {"products": dict, "branches": dict, "transactions": list, "batches": list}
        for key, expected_type in expected.items():
            if key not in data:
                raise InventoryError(f"Missing required data section: '{key}'.")
            if not isinstance(data[key], expected_type):
                raise InventoryError(f"'{key}' must be a {expected_type.__name__}.")
        if "physical_stock" in data and not isinstance(data["physical_stock"], dict):
            raise InventoryError("'physical_stock' must be a dictionary.")
        for product_id, product in data["products"].items():
            if not isinstance(product, dict):
                raise InventoryError(f"Product '{product_id}' must contain a dictionary of details.")
            if not isinstance(product.get("name"), str) or not product["name"].strip():
                raise InventoryError(f"Product '{product_id}' must have a non-empty name.")
            for field in ("price", "cost", "reorder_level"):
                if field not in product and field != "reorder_level":
                    raise InventoryError(f"Product '{product_id}' is missing '{field}'.")
                value = product.get(field, 0)
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value < 0):
                    raise InventoryError(f"Product '{product_id}' has an invalid '{field}'.")
        for branch_id, branch in data["branches"].items():
            if not isinstance(branch, dict) or not isinstance(branch.get("inventory", {}), dict):
                raise InventoryError(f"Branch '{branch_id}' or its inventory is invalid.")
            for product_id, quantity in branch.get("inventory", {}).items():
                if (isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                        or not math.isfinite(quantity) or quantity < 0 or not float(quantity).is_integer()):
                    raise InventoryError(f"Stock for '{product_id}' in branch '{branch_id}' must be a non-negative whole number.")
                if product_id not in data["products"]:
                    raise InventoryError(f"Branch '{branch_id}' contains unknown product '{product_id}'.")

        seen_transaction_ids = set()
        for index, transaction in enumerate(data["transactions"], start=1):
            if not isinstance(transaction, dict):
                raise InventoryError(f"Transaction #{index} must be a dictionary.")
            transaction_id = transaction.get("id")
            if not isinstance(transaction_id, str) or not transaction_id.strip():
                raise InventoryError(f"Transaction #{index} has an invalid ID.")
            is_successful = transaction.get("status", "SUCCESS") == "SUCCESS"
            if is_successful and transaction_id in seen_transaction_ids:
                raise InventoryError(f"Duplicate stored transaction ID: {transaction_id}.")
            if is_successful:
                seen_transaction_ids.add(transaction_id)
                product_id = transaction.get("product")
                kind = transaction.get("type")
                quantity = transaction.get("quantity")
                if product_id not in data["products"] or kind not in self.VALID_TYPES:
                    raise InventoryError(f"Stored transaction '{transaction_id}' has an invalid product or type.")
                if not self._is_whole_number(quantity) or quantity <= 0:
                    raise InventoryError(f"Stored transaction '{transaction_id}' has an invalid quantity.")
                if kind == "transfer":
                    if transaction.get("from_branch") not in data["branches"] or transaction.get("to_branch") not in data["branches"]:
                        raise InventoryError(f"Stored transaction '{transaction_id}' has an invalid transfer branch.")
                elif transaction.get("branch") not in data["branches"]:
                    raise InventoryError(f"Stored transaction '{transaction_id}' has an invalid branch.")
                # Snapshot legacy records once, so future price changes do not rewrite history.
                product = data["products"][product_id]
                transaction.setdefault("unit_price", product.get("price", 0))
                transaction.setdefault("unit_cost", product.get("cost", 0))
        for index, batch in enumerate(data["batches"], start=1):
            if not isinstance(batch, dict):
                raise InventoryError(f"Batch #{index} must be a dictionary.")
            batch_id = batch.get("batch")
            product_id = batch.get("product")
            branch_id = batch.get("branch")
            quantity = batch.get("quantity")
            if not isinstance(batch_id, str) or not batch_id.strip():
                raise InventoryError(f"Batch #{index} has an invalid batch ID.")
            if product_id not in data["products"]:
                raise InventoryError(f"Batch '{batch_id}' references an unknown product.")
            if branch_id not in data["branches"]:
                raise InventoryError(f"Batch '{batch_id}' references an unknown branch.")
            if not self._is_whole_number(quantity):
                raise InventoryError(f"Batch '{batch_id}' quantity must be a non-negative whole number.")
            if "expiry_date" not in batch:
                days = batch.get("expiry_days")
                if not self._is_whole_number(days):
                    raise InventoryError(f"Batch '{batch_id}' must have a valid expiry_date.")
                batch["expiry_date"] = (date.today() + timedelta(days=int(days))).isoformat()
            expiry = batch.get("expiry_date")
            if not isinstance(expiry, str):
                raise InventoryError(f"Batch '{batch_id}' expiry_date must be text in YYYY-MM-DD format.")
            try:
                date.fromisoformat(expiry)
            except ValueError as error:
                raise InventoryError(f"Batch '{batch_id}' has an invalid expiry_date; expected YYYY-MM-DD.") from error
        return data
    def save_data(self):
        self.data["products"] = self.products
        self.data["branches"] = self.branches
        self.data["transactions"] = self.transactions
        self.data["batches"] = self.batches
        self.data["physical_stock"] = self.physical_stock
        temporary_file = self.data_file.with_name(self.data_file.name + ".tmp")
        try:
            with temporary_file.open("w", encoding="utf-8") as file:
                json.dump(self._json_safe(self.data), file, indent=4,
                          ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_file, self.data_file)
        except (OSError, TypeError, ValueError) as error:
            try:
                temporary_file.unlink(missing_ok=True)
            except OSError:
                pass
            raise InventoryError(f"Could not save inventory data: {error}") from error
    def validate_transaction(self, transaction):
        if not isinstance(transaction, dict):
            return "Transaction must be a dictionary."
        required = {"id", "type", "product", "quantity"}
        missing = required - set(transaction)
        if missing:
            return "Missing field(s): " + ", ".join(sorted(missing))
        transaction_id = transaction["id"]
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            return "Transaction ID must be non-empty text."
        if any(isinstance(item, dict) and item.get("id") == transaction_id
               and item.get("status", "SUCCESS") == "SUCCESS"
               for item in self.transactions):
            return "Duplicate transaction ID."
        kind = transaction["type"]
        if not isinstance(kind, str) or kind not in self.VALID_TYPES:
            return "Invalid transaction type."
        product_id = transaction["product"]
        if not isinstance(product_id, str) or not product_id.strip():
            return "Product ID must be non-empty text."
        if product_id not in self.products:
            return "Invalid product."
        quantity = transaction["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity):
            return "Quantity must be a finite number."
        if quantity <= 0:
            return "Quantity must be greater than zero."
        if not self._is_whole_number(quantity):
            return "Quantity must be a whole number."
        if kind == "transfer":
            for field in ("from_branch", "to_branch"):
                if field not in transaction:
                    return f"Missing field: {field}."
                if not isinstance(transaction[field], str) or not transaction[field].strip():
                    return f"'{field}' must be non-empty text."
            source, destination = transaction["from_branch"], transaction["to_branch"]
            if source not in self.branches:
                return "Invalid source branch."
            if destination not in self.branches:
                return "Invalid destination branch."
            if source == destination:
                return "Source and destination branches cannot be the same."
        else:
            if "branch" not in transaction:
                return "Missing field: branch."
            branch_id = transaction["branch"]
            if not isinstance(branch_id, str) or not branch_id.strip():
                return "Branch ID must be non-empty text."
            if branch_id not in self.branches:
                return "Invalid branch."
        return "Valid"
    def _stock(self, branch_id, product_id):
        """Return available stock; a missing product entry means zero stock."""
        return self.branches[branch_id].setdefault("inventory", {}).get(product_id, 0)
    def _record_result(self, transaction, status, reason):
        """Store a transaction outcome once, including rejection reasons."""
        saved = self._json_safe(dict(transaction) if isinstance(transaction, dict)
                                else {"transaction": transaction})
        saved["status"] = status
        saved["reason"] = reason
        if status == "SUCCESS" and isinstance(saved.get("product"), str):
            product = self.products.get(saved["product"], {})
            saved["unit_price"] = product.get("price", 0)
            saved["unit_cost"] = product.get("cost", 0)
        self.transactions.append(saved)
        return saved
    def _reject(self, transaction, reason):
        if isinstance(transaction, dict):
            transaction_id = transaction.get("id")
            if isinstance(transaction_id, str) and transaction_id.strip():
                already_exists = any(
                    isinstance(item, dict) and item.get("id") == transaction_id
                    and item.get("status", "SUCCESS") == "SUCCESS"
                    for item in self.transactions
                )
                if not already_exists:
                    return self._record_result(transaction, "REJECTED", reason)
        return {"status": "REJECTED", "reason": reason, "transaction": transaction}
    def process_transaction(self, transaction):
        # Work on a copy so callers keep their original input unchanged.
        transaction = dict(transaction) if isinstance(transaction, dict) else transaction
        reason = self.validate_transaction(transaction)
        if reason != "Valid":
            return self._reject(transaction, reason)
        kind = transaction["type"]
        product_id = transaction["product"]
        quantity = int(transaction["quantity"])
        transaction["quantity"] = quantity

        if kind == "transfer":
            return self.transfer(transaction, validated=True)

        branch_id = transaction["branch"]
        available = self._stock(branch_id, product_id)
        if kind in {"sale", "supplier_return"} and quantity > available:
            return self._reject(
                transaction,
                f"Insufficient stock. Available: {available}, requested: {quantity}.",
            )
        if kind == "customer_return":
            remaining = self.remaining_returnable(product_id, branch_id)
            if quantity > remaining:
                return self._reject(
                    transaction,
                    f"Return exceeds original sales. Maximum returnable: {remaining}, requested: {quantity}.",
                )
        if kind in {"sale", "supplier_return"}:
            batch_error = self._consume_batches(branch_id, product_id, quantity)
            if batch_error:
                return self._reject(transaction, batch_error)
        inventory = self.branches[branch_id].setdefault("inventory", {})
        current = inventory.get(product_id, 0)
        if kind in {"purchase", "customer_return"}:
            tracked = any(b.get("branch") == branch_id and b.get("product") == product_id
                          for b in self.batches)
            if kind in {"purchase", "customer_return"} and tracked and transaction.get("expiry_date") is None:
                return self._reject(transaction, "expiry_date is required when adding stock to a batch-tracked product.")
            if transaction.get("expiry_date") is not None:
                try:
                    self._add_batch(transaction, branch_id, product_id, quantity)
                except InventoryError as error:
                    return self._reject(transaction, str(error))
            inventory[product_id] = current + quantity
        else:
            inventory[product_id] = current - quantity
        return self._record_result(transaction, "SUCCESS", f"{kind.replace('_', ' ').title()} processed successfully.")

    def _add_batch(self, transaction, branch_id, product_id, quantity):
        """Create a batch for incoming stock when an expiry date is supplied."""
        expiry = transaction.get("expiry_date")
        if not isinstance(expiry, str):
            raise InventoryError("expiry_date must be text in YYYY-MM-DD format.")
        try:
            date.fromisoformat(expiry)
        except ValueError as error:
            raise InventoryError("expiry_date must be text in YYYY-MM-DD format.") from error
        batch_id = transaction.get("batch_id", f"BATCH-{transaction['id']}")
        if not isinstance(batch_id, str) or not batch_id.strip() or any(
            b.get("batch") == batch_id for b in self.batches
        ):
            raise InventoryError("A unique, non-empty batch_id is required.")
        self.batches.append({"batch": batch_id, "product": product_id,
                             "branch": branch_id, "quantity": quantity,
                             "expiry_date": expiry})

    def _consume_batches(self, branch_id, product_id, quantity):
        """Consume non-expired tracked batches in FEFO order, or return an error."""
        candidates = [b for b in self.batches if b.get("branch") == branch_id
                      and b.get("product") == product_id and self._is_whole_number(b.get("quantity"))]
        if not candidates:
            return None  # This product/branch is not batch-tracked.
        today = date.today()
        usable = []
        for batch in candidates:
            try:
                expiry = date.fromisoformat(batch["expiry_date"])
            except (KeyError, TypeError, ValueError):
                return f"Invalid expiry date in batch {batch.get('batch', 'unknown')}."
            if expiry >= today and int(batch["quantity"]) > 0:
                usable.append(batch)
        usable.sort(key=lambda batch: batch["expiry_date"])
        available = sum(int(batch["quantity"]) for batch in usable)
        if available < quantity:
            return (f"Insufficient unexpired batch stock. Available: {available}, "
                    f"requested: {quantity}.")
        remaining = int(quantity)
        for batch in usable:
            if remaining <= 0:
                break
            stock = int(batch["quantity"])
            used = min(stock, remaining)
            batch["quantity"] = stock - used
            remaining -= used
        return None

    def transfer(self, transaction, validated=False):
        """Move stock atomically between two different branches."""
        if not validated:
            reason = self.validate_transaction(transaction)
            if reason != "Valid":
                return self._reject(transaction, reason)
        source = transaction["from_branch"]
        destination = transaction["to_branch"]
        product_id = transaction["product"]
        quantity = transaction["quantity"]
        available = self._stock(source, product_id)
        if quantity > available:
            return self._reject(
                transaction,
                f"Insufficient source stock. Available: {available}, requested: {quantity}.",
            )
        moved_batches, batch_error = self._move_batches(source, destination, product_id, quantity)
        if batch_error:
            return self._reject(transaction, batch_error)
        source_inventory = self.branches[source].setdefault("inventory", {})
        destination_inventory = self.branches[destination].setdefault("inventory", {})
        source_inventory[product_id] = available - quantity
        destination_inventory[product_id] = destination_inventory.get(product_id, 0) + quantity
        self.batches.extend(moved_batches)
        return self._record_result(transaction, "SUCCESS", "Transfer processed successfully.")
    def _move_batches(self, source, destination, product_id, quantity):
        """Build destination batches from unexpired source batches without mutating state."""
        candidates = [b for b in self.batches if b.get("branch") == source
                      and b.get("product") == product_id and self._is_whole_number(b.get("quantity"))]
        if not candidates:
            return [], None
        today = date.today()
        candidates = [b for b in candidates if date.fromisoformat(b["expiry_date"]) >= today]
        candidates.sort(key=lambda batch: batch["expiry_date"])
        if sum(int(b["quantity"]) for b in candidates) < quantity:
            return [], "Insufficient unexpired batch stock for transfer."
        remaining = quantity
        moved_batches = []
        for batch in candidates:
            if remaining <= 0:
                break
            moved = min(int(batch["quantity"]), remaining)
            if moved:
                batch["quantity"] = int(batch["quantity"]) - moved
                copied = dict(batch)
                copied["batch"] = f"{batch['batch']}-{destination}-{len(self.batches) + len(moved_batches) + 1}"
                copied["branch"] = destination
                copied["quantity"] = moved
                moved_batches.append(copied)
                remaining -= moved
        return moved_batches, None

    def remaining_returnable(self, product_id, branch_id=None):
        """Calculate successful sold units minus successful customer returns."""
        sold = returned = 0
        for item in self.transactions:
            if not isinstance(item, dict) or item.get("product") != product_id:
                continue
            if branch_id is not None and item.get("branch") != branch_id:
                continue
            if item.get("status", "SUCCESS") != "SUCCESS":
                continue
            quantity = item.get("quantity", 0)
            if (isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                    or not math.isfinite(quantity) or quantity <= 0 or not float(quantity).is_integer()):
                continue
            if item.get("type") == "sale":
                sold += quantity
            elif item.get("type") == "customer_return":
                returned += quantity
        return max(0, sold - returned)
    def process_sale(self, transaction_id, branch, product, quantity):
        return self.process_transaction({"id": transaction_id, "type": "sale", "branch": branch,
                                         "product": product, "quantity": quantity})
    def process_purchase(self, transaction_id, branch, product, quantity, expiry_date=None, batch_id=None):
        transaction = {"id": transaction_id, "type": "purchase", "branch": branch,
                       "product": product, "quantity": quantity}
        if expiry_date is not None:
            transaction["expiry_date"] = expiry_date
        if batch_id is not None:
            transaction["batch_id"] = batch_id
        return self.process_transaction(transaction)

    def process_customer_return(self, transaction_id, branch, product, quantity, expiry_date=None, batch_id=None):
        transaction = {"id": transaction_id, "type": "customer_return", "branch": branch,
                       "product": product, "quantity": quantity}
        if expiry_date is not None:
            transaction["expiry_date"] = expiry_date
        if batch_id is not None:
            transaction["batch_id"] = batch_id
        return self.process_transaction(transaction)
    def process_supplier_return(self, transaction_id, branch, product, quantity):
        return self.process_transaction({"id": transaction_id, "type": "supplier_return", "branch": branch,
                                         "product": product, "quantity": quantity})
    def product_expiry(self):
        """Print an expiry report; malformed and unknown batches are reported, not crashed."""
        print("----- PRODUCT EXPIRY REPORT -----")
        if not self.batches:
            print("No batches available.")
            return []
        report = []
        seen_batch_ids = set()
        for batch in self.batches:
            batch_id = batch.get("batch", "Unknown batch")
            product_id, branch_id, days = batch.get("product"), batch.get("branch"), batch.get("expiry_days")
            if batch.get("expiry_date"):
                try:
                    days = (date.fromisoformat(batch["expiry_date"]) - date.today()).days
                except (TypeError, ValueError):
                    days = None
            if not isinstance(batch_id, str) or not batch_id.strip():
                status = "INVALID: batch ID must be non-empty text"
            elif batch_id in seen_batch_ids:
                status = "INVALID: duplicate batch ID"
            else:
                seen_batch_ids.add(batch_id)
                if not isinstance(product_id, str) or product_id not in self.products or not isinstance(branch_id, str) or branch_id not in self.branches:
                    status = "INVALID: unknown product or branch"
                elif isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days):
                    status = "INVALID: expiry days must be a number"
                elif days <= 0:
                    status = "Expired"
                elif days <= 7:
                    status = "Critical"
                elif days <= 30:
                    status = "Expiring Soon"
                else:
                    status = "Safe"
            row = {"batch": batch_id, "product": product_id, "branch": branch_id,
                   "expiry_days": days, "status": status}
            report.append(row)
            print(f"Batch: {batch_id} | Product: {product_id} | Branch: {branch_id} | Days: {days} | Status: {status}")
        return report
    def low_stock_report(self):
        print("----- LOW STOCK / REORDER REPORT -----")
        report = []
        for branch_id, branch in self.branches.items():
            inventory = branch.get("inventory", {})
            print(f"Branch: {branch.get('name', branch_id)}")
            if not inventory:
                print("  Inventory is empty.")
            for product_id, product in self.products.items():
                quantity = inventory.get(product_id, 0)
                reorder_level = product.get("reorder_level", 0)
                if quantity <= reorder_level:
                    row = {"branch": branch_id, "product": product_id,
                           "quantity": quantity, "reorder_level": reorder_level}
                    report.append(row)
                    print(f"  REORDER REQUIRED: {product.get('name', product_id)} | Stock: {quantity} | Level: {reorder_level}")
        if not report:
            print("No products currently require reordering.")
        return report
    def stock_valuation(self):
        """Return branch and company-wide cost, sales, and potential profit."""
        print("----- STOCK VALUATION -----")
        company_cost = company_sales = total_units = 0
        branches_report = {}
        for branch_id, branch in self.branches.items():
            branch_cost = branch_sales = branch_units = 0
            for product_id, quantity in branch.get("inventory", {}).items():
                product = self.products.get(product_id)
                if product is None:
                    continue
                branch_cost += quantity * product.get("cost", 0)
                branch_sales += quantity * product.get("price", 0)
                branch_units += quantity
            branch_profit = branch_sales - branch_cost
            branches_report[branch_id] = {"units": branch_units, "cost": branch_cost,
                                          "potential_sales": branch_sales, "potential_profit": branch_profit}
            print(f"Branch: {branch.get('name', branch_id)} | Cost: ${branch_cost:.2f} | Sales: ${branch_sales:.2f} | Profit: ${branch_profit:.2f}")
            company_cost += branch_cost
            company_sales += branch_sales
            total_units += branch_units
        summary = {"branches": branches_report, "units": total_units, "cost": company_cost,
                   "potential_sales": company_sales, "potential_profit": company_sales - company_cost}
        print(f"Company totals | Units: {total_units} | Cost: ${company_cost:.2f} | Sales: ${company_sales:.2f} | Profit: ${company_sales - company_cost:.2f}")
        return summary
    def sales_analytics(self):
        """Calculate net sales after returns using prices recorded at transaction time."""
        product_units = {pid: 0 for pid in self.products}
        product_revenue = {pid: 0 for pid in self.products}
        product_profit = {pid: 0 for pid in self.products}
        branch_revenue = {bid: 0 for bid in self.branches}
        total_revenue = total_cost = total_units = 0
        for item in self.transactions:
            if not isinstance(item, dict) or item.get("status", "SUCCESS") != "SUCCESS":
                continue
            kind = item.get("type")
            if kind not in {"sale", "customer_return"}:
                continue
            product_id, branch_id = item.get("product"), item.get("branch")
            quantity = item.get("quantity", 0)
            if (product_id not in self.products or branch_id not in self.branches
                    or not self._is_whole_number(quantity) or quantity <= 0):
                continue
            sign = 1 if kind == "sale" else -1
            price = item.get("unit_price", self.products[product_id].get("price", 0))
            cost = item.get("unit_cost", self.products[product_id].get("cost", 0))
            revenue_delta = sign * quantity * price
            cost_delta = sign * quantity * cost
            total_revenue += revenue_delta
            total_cost += cost_delta
            total_units += sign * quantity
            product_units[product_id] += sign * quantity
            product_revenue[product_id] += revenue_delta
            product_profit[product_id] += revenue_delta - cost_delta
            branch_revenue[branch_id] += revenue_delta
        print("----- SALES ANALYTICS -----")
        print(f"Revenue: ${total_revenue:.2f} | Cost: ${total_cost:.2f} | Gross profit: ${total_revenue - total_cost:.2f}")
        print(f"Total units sold: {total_units}")
        result = {"revenue": total_revenue, "cost": total_cost,
                  "gross_profit": total_revenue - total_cost, "units": total_units}
        if not self.products or not any(product_units.values()):
            result["best_product"] = None
            result["worst_product"] = None
            print("No sales yet")
            return result
        best = max(product_units, key=product_units.get)
        worst = min(product_units, key=product_units.get)
        top_revenue = max(product_revenue, key=product_revenue.get)
        top_profit = max(product_profit, key=product_profit.get)
        top_branch = max(branch_revenue, key=branch_revenue.get) if branch_revenue else None
        result.update({"best_product": best, "worst_product": worst,
                       "highest_revenue_product": top_revenue,
                       "highest_profit_product": top_profit,
                       "highest_revenue_branch": top_branch})
        print(f"Best-selling product: {self.products[best].get('name', best)}")
        print(f"Worst-selling product: {self.products[worst].get('name', worst)}")
        print(f"Highest-revenue product: {self.products[top_revenue].get('name', top_revenue)}")
        print(f"Highest-profit product: {self.products[top_profit].get('name', top_profit)}")
        if top_branch is not None:
            print(f"Highest-revenue branch: {self.branches[top_branch].get('name', top_branch)}")
        return result

    def reconcile_inventory(self, physical_stock=None):
        physical_stock = self.physical_stock if physical_stock is None else physical_stock
        counts = {"MATCHED": 0, "SHORTAGE": 0, "EXCESS": 0}
        print("----- INVENTORY RECONCILIATION -----")
        if not isinstance(physical_stock, dict):
            print("Physical stock data must be a dictionary.")
            return counts
        for branch_id, physical_products in physical_stock.items():
            if branch_id not in self.branches:
                print(f"Unknown physical-stock branch: {branch_id}")
                continue
            if not isinstance(physical_products, dict):
                print(f"Invalid physical stock for branch: {branch_id}")
                continue
            print(f"Branch: {self.branches[branch_id].get('name', branch_id)}")
            inventory = self.branches[branch_id].get("inventory", {})
            for product_id, physical in physical_products.items():
                if product_id not in self.products:
                    print(f"  Unknown product: {product_id}")
                    continue
                system = inventory.get(product_id, 0)
                if (isinstance(physical, bool) or not isinstance(physical, (int, float))
                        or not math.isfinite(physical) or physical < 0 or not float(physical).is_integer()):
                    print(f"  Invalid physical quantity for {product_id}: {physical}")
                    continue
                difference = physical - system
                status = "MATCHED" if difference == 0 else "SHORTAGE" if difference < 0 else "EXCESS"
                counts[status] += 1
                print(f"  {self.products[product_id].get('name', product_id)} | System: {system} | Physical: {physical} | Difference: {difference} | Status: {status}")
            for product_id in self.products:
                if product_id not in physical_products:
                    print(f"  NOT COUNTED: {self.products[product_id].get('name', product_id)} | System: {inventory.get(product_id, 0)}")
        print(f"Reconciliation totals: {counts}")
        return counts

    def batch_stock_check(self):
        """Report branch/product stock whose batch totals do not match system stock."""
        print("----- BATCH / STOCK CHECK -----")
        mismatches = []
        tracked_pairs = {(batch.get("branch"), batch.get("product")) for batch in self.batches
                         if isinstance(batch, dict)}
        for branch_id, branch in self.branches.items():
            for product_id, system_quantity in branch.get("inventory", {}).items():
                if (branch_id, product_id) not in tracked_pairs:
                    continue
                batches_quantity = sum(
                    int(batch.get("quantity", 0)) for batch in self.batches
                    if batch.get("branch") == branch_id and batch.get("product") == product_id
                    and self._is_whole_number(batch.get("quantity", 0))
                )
                if batches_quantity != system_quantity:
                    row = {"branch": branch_id, "product": product_id,
                           "system_stock": system_quantity, "batch_stock": batches_quantity}
                    mismatches.append(row)
                    print(f"MISMATCH: {branch_id}/{product_id} | System: {system_quantity} | Batch total: {batches_quantity}")
        if not mismatches:
            print("All tracked batch totals match system stock.")
        return mismatches

    def dashboard(self, physical_stock=None):
        print(" INVENTORY MANAGEMENT DASHBOARD  ")
        successful = sum(1 for t in self.transactions if isinstance(t, dict) and t.get("status", "SUCCESS") == "SUCCESS")
        rejected = sum(1 for t in self.transactions if isinstance(t, dict) and t.get("status") == "REJECTED")
        print(f"Branches: {len(self.branches)}")
        print(f"Products: {len(self.products)}")
        print(f"Transactions | Processed: {len(self.transactions)} | Successful: {successful} | Rejected: {rejected}")
        self.stock_valuation()
        self.sales_analytics()
        self.low_stock_report()
        expiry = self.product_expiry()
        self.batch_stock_check()
        reconciliation = self.reconcile_inventory(physical_stock)
        print(f"Expiry batches: {len(expiry)} | Critical: {sum(1 for b in expiry if b['status'] == 'Critical')} | Expired: {sum(1 for b in expiry if b['status'] == 'Expired')}")
        print(f"Reconciliation: {reconciliation}")
def main():
    """Run a small interactive command-line inventory tool."""
    try:
        manager = InventoryManager()
    except InventoryError as error:
        print(f"Could not start Inventory Management System: {error}")
        return

    while True:
        print("\n===== INVENTORY MANAGEMENT =====")
        print("1. Sale")
        print("2. Purchase")
        print("3. Customer return")
        print("4. Supplier return")
        print("5. Transfer")
        print("6. Dashboard / reports")
        print("7. Save and exit")
        choice = input("Choose an option: ").strip()
        if choice == "7":
            try:
                manager.save_data()
                print("Data saved. Goodbye!")
            except InventoryError as error:
                print(error)
            return
        if choice == "6":
            manager.dashboard()
            continue
        if choice not in {"1", "2", "3", "4", "5"}:
            print("Invalid option. Choose 1-7.")
            continue
        try:
            transaction_id = input("Transaction ID: ").strip()
            product = input("Product ID: ").strip()
            quantity = int(input("Quantity (whole number): ").strip())
            if choice == "5":
                transaction = {"id": transaction_id, "type": "transfer", "product": product,
                               "quantity": quantity, "from_branch": input("From branch ID: ").strip(),
                               "to_branch": input("To branch ID: ").strip()}
            else:
                branch = input("Branch ID: ").strip()
                kind = {"1": "sale", "2": "purchase", "3": "customer_return",
                        "4": "supplier_return"}[choice]
                transaction = {"id": transaction_id, "type": kind, "product": product,
                               "quantity": quantity, "branch": branch}
                if kind == "purchase" and any(
                    b.get("branch") == branch and b.get("product") == product for b in manager.batches
                ):
                    transaction["expiry_date"] = input("Batch expiry date (YYYY-MM-DD): ").strip()
                    transaction["batch_id"] = input("New batch ID: ").strip()
            result = manager.process_transaction(transaction)
            print(f"{result['status']}: {result['reason']}")
        except (ValueError, EOFError, KeyboardInterrupt):
            print("Invalid input or cancelled operation.")
        except InventoryError as error:
            print(error)


if __name__ == "__main__":
    main()
