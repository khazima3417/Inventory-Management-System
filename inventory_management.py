import json
from pathlib import Path
DATA_FILE = Path(__file__).with_name("data.json")
VALID_TYPES = {"purchase", "sale", "customer_return", "supplier_return", "transfer"}
class InventoryError(Exception):
    """Base error for inventory operations."""
class InventoryManager:
    def __init__(self, data_file=DATA_FILE):
        self.data_file = Path(data_file)
        self.data = self.load_data()
        self.products = self.data["products"]
        self.branches = self.data["branches"]
        self.transactions = self.data["transactions"]
        self.batches = self.data["batches"]
        self.physical_stock = self.data.get("physical_stock", {})
        self.results = []
    def load_data(self):
        try:
            with open(self.data_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            raise InventoryError(f"Data file not found: {self.data_file}")
        except json.JSONDecodeError:
            raise InventoryError("data.json contains invalid JSON.")
        required_keys = {"products", "branches", "transactions", "batches"}
        missing = required_keys - set(data)
        if missing:
            raise InventoryError("Missing data sections: " + ", ".join(sorted(missing)))
        return data
    def save_data(self):
        self.data["products"] = self.products
        self.data["branches"] = self.branches
        self.data["transactions"] = self.transactions
        self.data["batches"] = self.batches
        self.data["physical_stock"] = self.physical_stock
        with open(self.data_file, "w", encoding="utf-8") as file:
            json.dump(self.data, file, indent=4)
    def validate_transaction(self, transaction):
        if not isinstance(transaction, dict):
            return "Transaction must be a dictionary."
        required = {"id", "type", "product", "quantity"}
        missing = required - set(transaction)
        if missing:
            return "Missing field(s): " + ", ".join(sorted(missing))
        transaction_id = transaction["id"]
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            return "Transaction ID must be a non-empty string."
        if any(item.get("id") == transaction_id for item in self.transactions):
            return "Duplicate transaction ID."
        transaction_type = transaction["type"]
        if transaction_type not in VALID_TYPES:
            return "Invalid transaction type."
        product_id = transaction["product"]
        if product_id not in self.products:
            return "Invalid product."
        quantity = transaction["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            return "Quantity must be a whole number."
        if quantity <= 0:
            return "Quantity must be greater than zero."
        if transaction_type == "transfer":
            required_branch_fields = {"from_branch", "to_branch"}
        else:
            required_branch_fields = {"branch"}
        missing = required_branch_fields - set(transaction)
        if missing:
            return "Missing field(s): " + ", ".join(sorted(missing))
        if transaction_type == "transfer":
            source = transaction["from_branch"]
            destination = transaction["to_branch"]
            if source not in self.branches:
                return "Invalid source branch."
            if destination not in self.branches:
                return "Invalid destination branch."
            if source == destination:
                return "Source and destination branches cannot be the same."
            if self.branches[source]["inventory"].get(product_id, 0) < quantity:
                available = self.branches[source]["inventory"].get(product_id, 0)
                return f"Insufficient stock in source branch. Available: {available}, requested: {quantity}."
        else:
            branch_id = transaction["branch"]
            if branch_id not in self.branches:
                return "Invalid branch."
            available = self.branches[branch_id]["inventory"].get(product_id, 0)
            if transaction_type in {"sale", "supplier_return"} and quantity > available:
                return f"Insufficient stock. Available: {available}, requested: {quantity}."
        if transaction_type == "customer_return":
            sold = self._quantity_for("sale", transaction["branch"], product_id)
            returned = self._quantity_for("customer_return", transaction["branch"], product_id)
            remaining = sold - returned
            if quantity > remaining:
                return f"Return rejected. Maximum returnable quantity: {max(0, remaining)}; requested: {quantity}"
        return "Valid"
    def _quantity_for(self, transaction_type, branch_id, product_id):
        total = 0
        for item in self.transactions:
            if (item.get("type") == transaction_type
                    and item.get("branch") == branch_id
                    and item.get("product") == product_id):
                total += item.get("quantity", 0)
        return total
    def process_transaction(self, transaction):
        reason = self.validate_transaction(transaction)
        if reason != "Valid":
            result = {"id": transaction.get("id", "UNKNOWN") if isinstance(transaction, dict) else "UNKNOWN",
                      "status": "REJECTED", "reason": reason}
            self.results.append(result)
            print(f'{result["status"]}: {result["reason"]}')
            return result
        kind = transaction["type"]
        product_id = transaction["product"]
        quantity = transaction["quantity"]
        if kind == "transfer":
            source = transaction["from_branch"]
            destination = transaction["to_branch"]
            self.branches[source]["inventory"][product_id] -= quantity
            self.branches[destination]["inventory"][product_id] = (
                self.branches[destination]["inventory"].get(product_id, 0) + quantity
            )
        else:
            branch_id = transaction["branch"]
            inventory = self.branches[branch_id]["inventory"]
            if kind in {"purchase", "customer_return"}:
                inventory[product_id] = inventory.get(product_id, 0) + quantity
            elif kind in {"sale", "supplier_return"}:
                inventory[product_id] -= quantity
        saved_transaction = dict(transaction)
        self.transactions.append(saved_transaction)
        result = {"id": transaction["id"], "status": "SUCCESS", "reason": "Transaction processed successfully."}
        self.results.append(result)
        print(f'SUCCESS: {transaction["id"]} - {kind}')
        return result
    def process_transactions(self, transaction_list):
        for transaction in transaction_list:
            self.process_transaction(transaction)
    def expiry_report(self):
        print("--- EXPIRY REPORT ---")
        if not self.batches:
            print("No batches available.")
            return
        for batch in self.batches:
            branch_id = batch.get("branch")
            if batch.get("product") not in self.products or branch_id not in self.branches:
                print(f'Unknown batch data: {batch.get("batch", "UNKNOWN")}')
                continue
            days = batch.get("expiry_days")
            if not isinstance(days, int):
                status = "INVALID EXPIRY"
            elif days <= 0:
                status = "Expired"
            elif days <= 7:
                status = "Critical"
            elif days <= 30:
                status = "Expiring Soon"
            else:
                status = "Safe"
            print(f'{self.branches[branch_id]["name"]} | {batch.get("batch")} | '
                  f'{self.products[batch["product"]]["name"]} | Qty: {batch.get("quantity", 0)} | {status}')
    def low_stock_report(self):
        print("---LOW STOCK / REORDER REPORT ---")
        low_stock_count = 0
        for branch_id, branch in self.branches.items():
            print(branch["name"] + ":")
            inventory = branch.get("inventory", {})
            if not inventory:
                print("  Inventory is empty.")
            for product_id, product in self.products.items():
                quantity = inventory.get(product_id, 0)
                reorder_level = product["reorder_level"]
                if quantity <= reorder_level:
                    low_stock_count += 1
                    print(f'  REORDER: {product["name"]} | Current: {quantity} | Level: {reorder_level}')
        print("Low-stock product entries:", low_stock_count)
        return low_stock_count
    def stock_valuation(self):
        print("--- STOCK VALUATION ---")
        company_cost = company_sales = company_profit = 0
        total_units = 0
        for branch in self.branches.values():
            branch_cost = branch_sales = 0
            for product_id, product in self.products.items():
                quantity = branch.get("inventory", {}).get(product_id, 0)
                branch_cost += quantity * product["cost"]
                branch_sales += quantity * product["price"]
                total_units += quantity
            branch_profit = branch_sales - branch_cost
            print(f'{branch["name"]}: Cost={branch_cost}, Potential Sales={branch_sales}, Potential Profit={branch_profit}')
            company_cost += branch_cost
            company_sales += branch_sales
            company_profit += branch_profit
        print(f"Company totals: units={total_units}, cost={company_cost}, potential sales={company_sales}, potential profit={company_profit}")
        return {"units": total_units, "cost": company_cost, "potential_sales": company_sales, "potential_profit": company_profit}
    def sales_analytics(self):
        total_revenue = total_cost = total_units = 0
        product_units = {}
        product_revenue = {}
        product_profit = {}
        branch_revenue = {}
        for transaction in self.transactions:
            if transaction.get("type") != "sale":
                continue
            product_id = transaction["product"]
            branch_id = transaction["branch"]
            quantity = transaction["quantity"]
            product = self.products[product_id]
            revenue = quantity * product["price"]
            cost = quantity * product["cost"]
            total_revenue += revenue
            total_cost += cost
            total_units += quantity
            product_units[product_id] = product_units.get(product_id, 0) + quantity
            product_revenue[product_id] = product_revenue.get(product_id, 0) + revenue
            product_profit[product_id] = product_profit.get(product_id, 0) + revenue - cost
            branch_revenue[branch_id] = branch_revenue.get(branch_id, 0) + revenue
        print("--- SALES ANALYTICS ---")
        print(f"Revenue: {total_revenue} | Cost of sold goods: {total_cost} | Gross profit: {total_revenue - total_cost}")
        print("Total units sold:", total_units)
        if not product_units:
            print("No sales transactions available.")
            return {"revenue": total_revenue, "cost": total_cost, "gross_profit": 0, "units": 0}
        best = max(product_units, key=product_units.get)
        worst = min(product_units, key=product_units.get)
        top_revenue_product = max(product_revenue, key=product_revenue.get)
        top_profit_product = max(product_profit, key=product_profit.get)
        top_branch = max(branch_revenue, key=branch_revenue.get)
        print("Best-selling product:", self.products[best]["name"])
        print("Worst-selling product:", self.products[worst]["name"])
        print("Highest-revenue product:", self.products[top_revenue_product]["name"])
        print("Highest-profit product:", self.products[top_profit_product]["name"])
        print("Highest-revenue branch:", self.branches[top_branch]["name"])
        return {"revenue": total_revenue, "cost": total_cost, "gross_profit": total_revenue - total_cost,
                "units": total_units, "best_product": best, "worst_product": worst,
                "highest_revenue_product": top_revenue_product, "highest_profit_product": top_profit_product,
                "highest_revenue_branch": top_branch}
    def reconcile_inventory(self):
        print("--- INVENTORY RECONCILIATION ---")
        counts = {"MATCHED": 0, "SHORTAGE": 0, "EXCESS": 0}
        for branch_id, physical_products in self.physical_stock.items():
            if branch_id not in self.branches:
                print("Unknown physical-stock branch:", branch_id)
                continue
            branch = self.branches[branch_id]
            for product_id, physical in physical_products.items():
                if product_id not in self.products:
                    print("Unknown physical-stock product:", product_id)
                    continue
                system = branch.get("inventory", {}).get(product_id, 0)
                difference = physical - system
                if difference == 0:
                    status = "MATCHED"
                elif difference < 0:
                    status = "SHORTAGE"
                else:
                    status = "EXCESS"
                counts[status] += 1
                print(f'{branch["name"]} | {self.products[product_id]["name"]} | '
                      f'System: {system} | Physical: {physical} | Difference: {difference} | {status}')
        print("Totals:", counts)
        return counts
    def dashboard(self):
        valuation = self.stock_valuation()
        sales = self.sales_analytics()
        low_stock = self.low_stock_report()
        expired = sum(1 for batch in self.batches if isinstance(batch.get("expiry_days"), int) and batch["expiry_days"] <= 0)
        critical = sum(1 for batch in self.batches if isinstance(batch.get("expiry_days"), int) and 1 <= batch["expiry_days"] <= 7)
        successful = sum(1 for item in self.results if item["status"] == "SUCCESS")
        rejected = sum(1 for item in self.results if item["status"] == "REJECTED")
        reconciliation = self.reconcile_inventory()
        print("INVENTORY MANAGEMENT DASHBOARD")
        print(f"Branches: {len(self.branches)}")
        print(f"Products: {len(self.products)}")
        print("Transactions")
        print(f"Processed this session: {len(self.results)}")
        print(f"Successful this session: {successful}")
        print(f"Rejected this session: {rejected}")
        print("Inventory")
        print(f"Total Units: {valuation['units']}")
        print(f"Inventory Cost Value: {valuation['cost']}")
        print(f"Potential Sales Value: {valuation['potential_sales']}")
        print(f"Potential Profit: {valuation['potential_profit']}")
        print("Sales")
        print(f"Revenue: {sales['revenue']}")
        print(f"Cost: {sales['cost']}")
        print(f"Gross Profit: {sales['gross_profit']}")
        print("Top Product:", self.products[sales["best_product"]]["name"] if "best_product" in sales else "N/A")
        print("Top Branch:", self.branches[sales["highest_revenue_branch"]]["name"] if "highest_revenue_branch" in sales else "N/A")
        print("Stock Alerts")
        print("Low Stock Entries:", low_stock)
        print("Critical Expiry Batches:", critical)
        print("Expired Batches:", expired)
        print("Reconciliation")
        for status, count in reconciliation.items():
            print(f"{status.title()}: {count}")
    def run_demo(self):
        self.process_transaction({
            "id": "T004", "type": "transfer", "from_branch": "B001",
            "to_branch": "B002", "product": "P001", "quantity": 10
        })
        self.expiry_report()
        self.low_stock_report()
        self.stock_valuation()
        self.sales_analytics()
        self.reconcile_inventory()
        self.dashboard()
if __name__ == "__main__":
    try:
        manager = InventoryManager()
        manager.run_demo()
        manager.save_data()
    except (InventoryError, OSError) as error:
        print("Program could not finish:", error)
