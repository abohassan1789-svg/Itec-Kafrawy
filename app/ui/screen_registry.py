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
    "customer_groups": _customer_groups,
    "customers": _customers,
    "projects": _projects,
    "supplier_groups": _supplier_groups,
    "company": _company,
    "fiscal_years": _fiscal_years,
    "currencies": _currencies,
    "tax_codes": _tax_codes,
    "users": _users,
    "roles": _roles,
    "user_permissions": _user_permissions,
    "login_logs": _login_logs,
}
