"""ربط مفتاح الشاشة (target_code في permission_registry) بالكلاس اللي بيبنيها.

كل شاشة بتفتح في شباك لوحدها مكبّر. الشاشة اللي لسه مالهاش factory هنا بتظهر في القائمة
(لو ليك صلاحية عليها) وبتقول «لسه بتتبني».
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QWidget

ScreenFactory = Callable[[], QWidget]


def _chart_of_accounts() -> QWidget:
    from app.ui.screens.chart_of_accounts_screen import ChartOfAccountsScreen

    return ChartOfAccountsScreen()


def _default_accounts() -> QWidget:
    from app.ui.screens.default_accounts_screen import DefaultAccountsScreen

    return DefaultAccountsScreen()


def _cost_centers() -> QWidget:
    from app.ui.screens.cost_centers_screen import CostCentersScreen

    return CostCentersScreen()


def _customer_groups() -> QWidget:
    from app.ui.screens.customer_groups_screen import CustomerGroupsScreen

    return CustomerGroupsScreen()


def _supplier_groups() -> QWidget:
    from app.ui.screens.supplier_groups_screen import SupplierGroupsScreen

    return SupplierGroupsScreen()


def _suppliers() -> QWidget:
    from app.ui.screens.suppliers_screen import SuppliersScreen

    return SuppliersScreen()


def _employees() -> QWidget:
    from app.ui.screens.employees_screen import EmployeesScreen

    return EmployeesScreen()


def _partners() -> QWidget:
    from app.ui.screens.partners_screen import PartnersScreen

    return PartnersScreen()


def _treasuries() -> QWidget:
    from app.ui.screens.treasuries_screen import TreasuriesScreen

    return TreasuriesScreen()


def _banks() -> QWidget:
    from app.ui.screens.banks_screen import BanksScreen

    return BanksScreen()


def _customer_receipts() -> QWidget:
    from app.ui.screens.vouchers_screen import ReceiptsScreen

    return ReceiptsScreen()


def _supplier_payments() -> QWidget:
    from app.ui.screens.vouchers_screen import PaymentsScreen

    return PaymentsScreen()


def _expense_types() -> QWidget:
    from app.ui.screens.expense_types_screen import ExpenseTypesScreen

    return ExpenseTypesScreen()


def _warehouses() -> QWidget:
    from app.ui.screens.warehouses_screen import WarehousesScreen

    return WarehousesScreen()


def _units_brands() -> QWidget:
    from app.ui.screens.units_brands_screen import UnitsBrandsScreen

    return UnitsBrandsScreen()


def _item_categories() -> QWidget:
    from app.ui.screens.item_categories_screen import ItemCategoriesScreen

    return ItemCategoriesScreen()


def _items() -> QWidget:
    from app.ui.screens.items_screen import ItemsScreen

    return ItemsScreen()


def _journal_entries() -> QWidget:
    from app.ui.screens.journal_entries_screen import JournalEntriesScreen

    return JournalEntriesScreen()


def _opening_balances() -> QWidget:
    from app.ui.screens.opening_balances_screen import OpeningBalancesScreen

    return OpeningBalancesScreen()


def _account_statement() -> QWidget:
    from app.ui.screens.account_statement_screen import AccountStatementScreen

    return AccountStatementScreen()


def _trial_balance() -> QWidget:
    from app.ui.screens.trial_balance_screen import TrialBalanceScreen

    return TrialBalanceScreen()


def _customers() -> QWidget:
    from app.ui.screens.customers_screen import CustomersScreen

    return CustomersScreen()


def _projects() -> QWidget:
    from app.ui.screens.projects_screen import ProjectsScreen

    return ProjectsScreen()


def _company() -> QWidget:
    from app.ui.screens.company_screen import CompanyScreen

    return CompanyScreen()


def _fiscal_years() -> QWidget:
    from app.ui.screens.fiscal_years_screen import FiscalYearsScreen

    return FiscalYearsScreen()


def _currencies() -> QWidget:
    from app.ui.screens.currencies_screen import CurrenciesScreen

    return CurrenciesScreen()


def _tax_codes() -> QWidget:
    from app.ui.screens.tax_codes_screen import TaxCodesScreen

    return TaxCodesScreen()


def _users() -> QWidget:
    from app.ui.screens.users_screen import UsersScreen

    return UsersScreen()


def _roles() -> QWidget:
    from app.ui.screens.roles_screen import RolesScreen

    return RolesScreen()


def _user_permissions() -> QWidget:
    from app.ui.screens.user_permissions_screen import UserPermissionsScreen

    return UserPermissionsScreen()


def _login_logs() -> QWidget:
    from app.ui.screens.login_logs_screen import LoginLogsScreen

    return LoginLogsScreen()


# المفتاح ← دالة بتبني الشاشة (import جوه الدالة عشان فتح البرنامج يفضل سريع).
SCREEN_FACTORIES: dict[str, ScreenFactory] = {
    "chart_of_accounts": _chart_of_accounts,
    "default_accounts": _default_accounts,
    "cost_centers": _cost_centers,
    "journal_entries": _journal_entries,
    "opening_balances": _opening_balances,
    "account_statement": _account_statement,
    "trial_balance": _trial_balance,
    "customer_groups": _customer_groups,
    "customers": _customers,
    "projects": _projects,
    "supplier_groups": _supplier_groups,
    "suppliers": _suppliers,
    "employees": _employees,
    "partners": _partners,
    "treasuries": _treasuries,
    "banks": _banks,
    "customer_receipts": _customer_receipts,
    "supplier_payments": _supplier_payments,
    "expense_types": _expense_types,
    "warehouses": _warehouses,
    "units_brands": _units_brands,
    "item_categories": _item_categories,
    "items": _items,
    "company": _company,
    "fiscal_years": _fiscal_years,
    "currencies": _currencies,
    "tax_codes": _tax_codes,
    "users": _users,
    "roles": _roles,
    "user_permissions": _user_permissions,
    "login_logs": _login_logs,
}
