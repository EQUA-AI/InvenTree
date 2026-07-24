"""
Write Tools Package

Write tools that modify data in InvenTree.
These tools may require HITL (Human-in-the-Loop) approval for sensitive operations.
"""

from ai.core.tools.inventree.write._registry import WRITE_TOOLS
from ai.core.tools.inventree.write.addresses import (
    ADDRESS_WRITE_TOOLS,
    create_company_address,
    delete_company_address,
    delete_company_contact,
    update_company_address,
    update_company_contact,
)
from ai.core.tools.inventree.write.attachments import (
    ATTACHMENT_WRITE_TOOLS,
    add_part_attachment,
    add_stock_attachment,
    create_label_template,
    delete_attachment,
    print_label,
)
from ai.core.tools.inventree.write.bom import (
    BOM_WRITE_TOOLS,
    add_bom_item,
    add_bom_substitute,
    delete_bom_item,
    update_bom_item,
    validate_bom,
)
from ai.core.tools.inventree.write.build_orders import (
    BUILD_ORDER_WRITE_TOOLS,
    allocate_build_stock,
    cancel_build_order,
    complete_build_output,
    create_build_order,
    issue_build_order,
)
from ai.core.tools.inventree.write.build_orders_advanced import (
    BUILD_ORDER_ADVANCED_WRITE_TOOLS,
    auto_allocate_build,
    finish_build_order,
    hold_build_order,
    unallocate_build_stock,
    update_build_order,
)
from ai.core.tools.inventree.write.categories import (
    CATEGORY_WRITE_TOOLS,
    create_part_category,
    create_stock_location,
    delete_stock_location,
    update_part_category,
)
from ai.core.tools.inventree.write.companies import (
    COMPANY_WRITE_TOOLS,
    create_company,
    create_company_contact,
    create_manufacturer_part,
    create_supplier_part,
    update_company,
)
from ai.core.tools.inventree.write.notifications import (
    NOTIFICATION_WRITE_TOOLS,
    create_notification,
    delete_notification,
    mark_all_notifications_read,
    mark_notification_read,
    send_stock_alert,
)
from ai.core.tools.inventree.write.parameters import (
    PARAMETER_WRITE_TOOLS,
    bulk_set_parameters,
    copy_parameters,
    create_parameter_template,
    delete_parameter_template,
    update_parameter_template,
)
from ai.core.tools.inventree.write.parts import (
    PART_WRITE_TOOLS,
    create_part,
    deactivate_part,
    duplicate_part,
    set_part_parameter,
    update_part,
)
from ai.core.tools.inventree.write.project_codes import (
    PROJECT_CODE_WRITE_TOOLS,
    assign_project_code,
    create_project_code,
    delete_project_code,
    remove_project_code,
    update_project_code,
)
from ai.core.tools.inventree.write.purchase_orders import (
    PURCHASE_ORDER_WRITE_TOOLS,
    add_po_line_item,
    cancel_purchase_order,
    create_purchase_order,
    issue_purchase_order,
    receive_po_items,
)
from ai.core.tools.inventree.write.return_orders import (
    RETURN_ORDER_WRITE_TOOLS,
    add_ro_line_item,
    complete_return_order,
    create_return_order,
    issue_return_order,
    receive_ro_items,
)
from ai.core.tools.inventree.write.sales_orders import (
    SALES_ORDER_WRITE_TOOLS,
    add_so_line_item,
    allocate_so_stock,
    create_sales_order,
    create_so_shipment,
    issue_sales_order,
)
from ai.core.tools.inventree.write.sales_orders_advanced import (
    SALES_ORDER_ADVANCED_WRITE_TOOLS,
    cancel_sales_order,
    complete_sales_order,
    hold_sales_order,
    ship_so_shipment,
    update_sales_order,
)
from ai.core.tools.inventree.write.settings import (
    SETTINGS_WRITE_TOOLS,
    create_custom_state,
    delete_custom_state,
    update_custom_state,
    update_global_setting,
    update_user_setting,
)
from ai.core.tools.inventree.write.stock import (
    STOCK_WRITE_TOOLS,
    add_stock,
    count_stock,
    merge_stock,
    remove_stock,
    transfer_stock,
)
from ai.core.tools.inventree.write.stock_advanced import (
    STOCK_ADVANCED_WRITE_TOOLS,
    assign_stock,
    install_stock,
    return_stock,
    serialize_stock,
    uninstall_stock,
)
from ai.core.tools.inventree.write.stock_operations import (
    STOCK_OPERATIONS_WRITE_TOOLS,
    add_stock_test_result,
    change_stock_status,
    convert_stock,
    split_stock,
    update_stock_location,
)
from ai.core.tools.inventree.write.supplier_parts import (
    SUPPLIER_PART_WRITE_TOOLS,
    add_supplier_price_break,
    delete_manufacturer_part,
    delete_supplier_part,
    update_manufacturer_part,
    update_supplier_part,
)
from ai.core.tools.inventree.write.test_templates import (
    TEST_TEMPLATE_WRITE_TOOLS,
    create_test_template,
    delete_stock_test_result,
    delete_test_template,
    update_stock_test_result,
    update_test_template,
)

__all__ = [
    # Address tools
    "ADDRESS_WRITE_TOOLS",
    # Attachment tools
    "ATTACHMENT_WRITE_TOOLS",
    "BOM_WRITE_TOOLS",
    "BUILD_ORDER_ADVANCED_WRITE_TOOLS",
    "BUILD_ORDER_WRITE_TOOLS",
    # Category tools
    "CATEGORY_WRITE_TOOLS",
    "COMPANY_WRITE_TOOLS",
    # Notification tools
    "NOTIFICATION_WRITE_TOOLS",
    # Parameter tools
    "PARAMETER_WRITE_TOOLS",
    # Tool collections
    "PART_WRITE_TOOLS",
    # Project code tools
    "PROJECT_CODE_WRITE_TOOLS",
    "PURCHASE_ORDER_WRITE_TOOLS",
    "RETURN_ORDER_WRITE_TOOLS",
    "SALES_ORDER_ADVANCED_WRITE_TOOLS",
    "SALES_ORDER_WRITE_TOOLS",
    # Settings tools
    "SETTINGS_WRITE_TOOLS",
    "STOCK_ADVANCED_WRITE_TOOLS",
    "STOCK_OPERATIONS_WRITE_TOOLS",
    "STOCK_WRITE_TOOLS",
    # Supplier part tools
    "SUPPLIER_PART_WRITE_TOOLS",
    # Test template tools
    "TEST_TEMPLATE_WRITE_TOOLS",
    "WRITE_TOOLS",
    # BOM tools
    "add_bom_item",
    "add_bom_substitute",
    "add_part_attachment",
    "add_po_line_item",
    "add_ro_line_item",
    "add_so_line_item",
    # Stock tools
    "add_stock",
    "add_stock_attachment",
    "add_stock_test_result",
    "add_supplier_price_break",
    "allocate_build_stock",
    "allocate_so_stock",
    "assign_project_code",
    "assign_stock",
    "auto_allocate_build",
    "bulk_set_parameters",
    "cancel_build_order",
    "cancel_purchase_order",
    "cancel_sales_order",
    # Stock operation tools
    "change_stock_status",
    "complete_build_output",
    "complete_return_order",
    "complete_sales_order",
    "convert_stock",
    "copy_parameters",
    "count_stock",
    # Build order tools
    "create_build_order",
    # Company tools
    "create_company",
    "create_company_address",
    "create_company_contact",
    "create_custom_state",
    "create_label_template",
    "create_manufacturer_part",
    "create_notification",
    "create_parameter_template",
    # Part tools
    "create_part",
    "create_part_category",
    "create_project_code",
    # Purchase order tools
    "create_purchase_order",
    # Return order tools
    "create_return_order",
    # Sales order tools
    "create_sales_order",
    "create_so_shipment",
    "create_stock_location",
    "create_supplier_part",
    "create_test_template",
    "deactivate_part",
    "delete_attachment",
    "delete_bom_item",
    "delete_company_address",
    "delete_company_contact",
    "delete_custom_state",
    "delete_manufacturer_part",
    "delete_notification",
    "delete_parameter_template",
    "delete_project_code",
    "delete_stock_location",
    "delete_stock_test_result",
    "delete_supplier_part",
    "delete_test_template",
    "duplicate_part",
    "finish_build_order",
    # Build order advanced tools
    "hold_build_order",
    "hold_sales_order",
    "install_stock",
    "issue_build_order",
    "issue_purchase_order",
    "issue_return_order",
    "issue_sales_order",
    "mark_all_notifications_read",
    "mark_notification_read",
    "merge_stock",
    "print_label",
    "receive_po_items",
    "receive_ro_items",
    "remove_project_code",
    "remove_stock",
    "return_stock",
    "send_stock_alert",
    # Advanced stock tools
    "serialize_stock",
    "set_part_parameter",
    # Sales order advanced tools
    "ship_so_shipment",
    "split_stock",
    "transfer_stock",
    "unallocate_build_stock",
    "uninstall_stock",
    "update_bom_item",
    "update_build_order",
    "update_company",
    "update_company_address",
    "update_company_contact",
    "update_custom_state",
    "update_global_setting",
    "update_manufacturer_part",
    "update_parameter_template",
    "update_part",
    "update_part_category",
    "update_project_code",
    "update_sales_order",
    "update_stock_location",
    "update_stock_test_result",
    "update_supplier_part",
    "update_test_template",
    "update_user_setting",
    "validate_bom",
]
