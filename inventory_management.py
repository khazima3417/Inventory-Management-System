import json
import math
from pathlib import Path
class InventoryError(Exception):
    """Base error for inventory operations."""
class InventoryManager:
    """Manage products, branch stock, transactions, batches, and reports."""
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
            for field in ("price", "cost", "reorder_level"):
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

        for index, transaction in enumerate(data["transactions"], start=1):
            if not isinstance(transaction, dict):
                raise InventoryError(f"Transaction #{index} must be a dictionary.")
        for index, batch in enumerate(data["batches"], start=1):
            if not isinstance(batch, dict):
                raise InventoryError(f"Batch #{index} must be a dictionary.")
        return data
    def save_data(self):
        self.data["products"] = self.products
        self.data["branches"] = self.branches
        self.data["transactions"] = self.transactions
        self.data["batches"] = self.batches
        self.data["physical_stock"] = self.physical_stock
        try:
            with self.data_file.open("w", encoding="utf-8") as file:
                json.dump(self.data, file, indent=4, ensure_ascii=False, allow_nan=False)
        except (OSError, TypeError, ValueError) as error:
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
        if any(isinstance(item, dict) and item.get("id") == transaction_id for item in self.transactions):
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
        if not float(quantity).is_integer():
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
        saved = dict(transaction) if isinstance(transaction, dict) else {"transaction": transaction}
        saved["status"] = status
        saved["reason"] = reason
        self.transactions.append(saved)
        print(f"{status}: {reason}")
        return saved
    def _reject(self, transaction, reason):
        if isinstance(transaction, dict):
            transaction_id = transaction.get("id")
            if isinstance(transaction_id, str) and transaction_id.strip():
                already_exists = any(
                    isinstance(item, dict) and item.get("id") == transaction_id
                    for item in self.transactions
                )
                if not already_exists:
                    return self._record_result(transaction, "REJECTED", reason)
        print(f"REJECTED: {reason}")
        return {"status": "REJECTED", "reason": reason, "transaction": transaction}
    def process_transaction(self, transaction):
        reason = self.validate_transaction(transaction)
        if reason != "Valid":
            return self._reject(transaction, reason)
        kind = transaction["type"]
        product_id = transaction["product"]
        quantity = transaction["quantity"]

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
        inventory = self.branches[branch_id].setdefault("inventory", {})
        current = inventory.get(product_id, 0)
        if kind in {"purchase", "customer_return"}:
            inventory[product_id] = current + quantity
        else:  
            inventory[product_id] = current - quantity
        return self._record_result(transaction, "SUCCESS", f"{kind.replace('_', ' ').title()} processed successfully.")

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
        source_inventory = self.branches[source].setdefault("inventory", {})
        destination_inventory = self.branches[destination].setdefault("inventory", {})
        source_inventory[product_id] = available - quantity
        destination_inventory[product_id] = destination_inventory.get(product_id, 0) + quantity
        return self._record_result(transaction, "SUCCESS", "Transfer processed successfully.")
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
    def process_purchase(self, transaction_id, branch, product, quantity):
        return self.process_transaction({"id": transaction_id, "type": "purchase", "branch": branch,
                                         "product": product, "quantity": quantity})
    def process_customer_return(self, transaction_id, branch, product, quantity):
        return self.process_transaction({"id": transaction_id, "type": "customer_return", "branch": branch,
                                         "product": product, "quantity": quantity})
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
        """Calculate sales analytics using successful historical sales only."""
        total_revenue = total_cost = total_units = 0
        product_units, product_revenue, product_profit, branch_revenue = {}, {}, {}, {}
        for item in self.transactions:
            if not isinstance(item, dict) or item.get("type") != "sale" or item.get("status", "SUCCESS") != "SUCCESS":
                continue
            product_id, branch_id, quantity = item.get("product"), item.get("branch"), item.get("quantity", 0)
            if product_id not in self.products or branch_id not in self.branches:
                continue
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity <= 0:
                continue
            product = self.products[product_id]
            revenue, cost = quantity * product.get("price", 0), quantity * product.get("cost", 0)
            total_revenue += revenue
            total_cost += cost
            total_units += quantity
            product_units[product_id] = product_units.get(product_id, 0) + quantity
            product_revenue[product_id] = product_revenue.get(product_id, 0) + revenue
            product_profit[product_id] = product_profit.get(product_id, 0) + revenue - cost
            branch_revenue[branch_id] = branch_revenue.get(branch_id, 0) + revenue
        print("----- SALES ANALYTICS -----")
        print(f"Revenue: ${total_revenue:.2f} | Cost: ${total_cost:.2f} | Gross profit: ${total_revenue - total_cost:.2f}")
        print(f"Total units sold: {total_units}")
        result = {"revenue": total_revenue, "cost": total_cost, "gross_profit": total_revenue - total_cost,
                  "units": total_units}
        if not product_units:
            print("No successful sales available for analytics.")
            return result
        best = max(product_units, key=product_units.get)
        worst = min(product_units, key=product_units.get)
        top_revenue = max(product_revenue, key=product_revenue.get)
        top_profit = max(product_profit, key=product_profit.get)
        top_branch = max(branch_revenue, key=branch_revenue.get)
        result.update({"best_product": best, "worst_product": worst,
                       "highest_revenue_product": top_revenue, "highest_profit_product": top_profit,
                       "highest_revenue_branch": top_branch})
        print(f"Best-selling product: {self.products[best].get('name', best)}")
        print(f"Worst-selling product: {self.products[worst].get('name', worst)}")
        print(f"Highest-revenue product: {self.products[top_revenue].get('name', top_revenue)}")
        print(f"Highest-profit product: {self.products[top_profit].get('name', top_profit)}")
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
        print(f"Reconciliation totals: {counts}")
        return counts

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
        reconciliation = self.reconcile_inventory(physical_stock)
        print(f"Expiry batches: {len(expiry)} | Critical: {sum(1 for b in expiry if b['status'] == 'Critical')} | Expired: {sum(1 for b in expiry if b['status'] == 'Expired')}")
        print(f"Reconciliation: {reconciliation}")
def main():
    try:
        manager = InventoryManager()
    except InventoryError as error:
        print(f"Could not start Inventory Management System: {error}")
        return
    manager.dashboard()
    try:
        manager.save_data()
        print("Data saved successfully to data.json")
    except InventoryError as error:
        print(f"Could not save data.json: {error}")


if __name__ == "__main__":
    main()
