"""
Write Tools Package

Write tools that modify data in InvenTree.
These tools may require HITL (Human-in-the-Loop) approval for sensitive operations.
"""

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
from ai.core.tools.inventree.write.parts import (
    PART_WRITE_TOOLS,
    create_part,
    deactivate_part,
    duplicate_part,
    set_part_parameter,
    update_part,
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
from ai.core.tools.inventree.write.companies import (
    COMPANY_WRITE_TOOLS,
    create_company,
    create_company_contact,
    create_manufacturer_part,
    create_supplier_part,
    update_company,
)
from ai.core.tools.inventree.write.categories import (
    CATEGORY_WRITE_TOOLS,
    create_part_category,
    update_part_category,
    create_stock_location,
    delete_stock_location,
)
from ai.core.tools.inventree.write.attachments import (
    ATTACHMENT_WRITE_TOOLS,
    add_part_attachment,
    delete_attachment,
    add_stock_attachment,
    print_label,
    create_label_template,
)
from ai.core.tools.inventree.write.parameters import (
    PARAMETER_WRITE_TOOLS,
    create_parameter_template,
    update_parameter_template,
    delete_parameter_template,
    bulk_set_parameters,
    copy_parameters,
)
from ai.core.tools.inventree.write.test_templates import (
    TEST_TEMPLATE_WRITE_TOOLS,
    create_test_template,
    update_test_template,
    delete_test_template,
    update_stock_test_result,
    delete_stock_test_result,
)
from ai.core.tools.inventree.write.notifications import (
    NOTIFICATION_WRITE_TOOLS,
    mark_notification_read,
    mark_all_notifications_read,
    delete_notification,
    create_notification,
    send_stock_alert,
)
from ai.core.tools.inventree.write.addresses import (
    ADDRESS_WRITE_TOOLS,
    create_company_address,
    update_company_address,
    delete_company_address,
    update_company_contact,
    delete_company_contact,
)
from ai.core.tools.inventree.write.project_codes import (
    PROJECT_CODE_WRITE_TOOLS,
    create_project_code,
    update_project_code,
    delete_project_code,
    assign_project_code,
    remove_project_code,
)
from ai.core.tools.inventree.write.settings import (
    SETTINGS_WRITE_TOOLS,
    update_global_setting,
    update_user_setting,
    create_custom_state,
    update_custom_state,
    delete_custom_state,
)
from ai.core.tools.inventree.write.supplier_parts import (
    SUPPLIER_PART_WRITE_TOOLS,
    update_supplier_part,
    delete_supplier_part,
    update_manufacturer_part,
    delete_manufacturer_part,
    add_supplier_price_break,
)

# Combine all write tools
WRITE_TOOLS = [
    *PART_WRITE_TOOLS,
    *STOCK_WRITE_TOOLS,
    *STOCK_ADVANCED_WRITE_TOOLS,
    *STOCK_OPERATIONS_WRITE_TOOLS,
    *BOM_WRITE_TOOLS,
    *PURCHASE_ORDER_WRITE_TOOLS,
    *SALES_ORDER_WRITE_TOOLS,
    *SALES_ORDER_ADVANCED_WRITE_TOOLS,
    *BUILD_ORDER_WRITE_TOOLS,
    *BUILD_ORDER_ADVANCED_WRITE_TOOLS,
    *RETURN_ORDER_WRITE_TOOLS,
    *COMPANY_WRITE_TOOLS,
    *CATEGORY_WRITE_TOOLS,
    *ATTACHMENT_WRITE_TOOLS,
    *PARAMETER_WRITE_TOOLS,
    *TEST_TEMPLATE_WRITE_TOOLS,
    *NOTIFICATION_WRITE_TOOLS,
    *ADDRESS_WRITE_TOOLS,
    *PROJECT_CODE_WRITE_TOOLS,
    *SETTINGS_WRITE_TOOLS,
    *SUPPLIER_PART_WRITE_TOOLS,
]

__all__ = [
    "WRITE_TOOLS",
    # Tool collections
    "PART_WRITE_TOOLS",
    "STOCK_WRITE_TOOLS",
    "STOCK_ADVANCED_WRITE_TOOLS",
    "STOCK_OPERATIONS_WRITE_TOOLS",
    "BOM_WRITE_TOOLS",
    "PURCHASE_ORDER_WRITE_TOOLS",
    "SALES_ORDER_WRITE_TOOLS",
    "SALES_ORDER_ADVANCED_WRITE_TOOLS",
    "BUILD_ORDER_WRITE_TOOLS",
    "BUILD_ORDER_ADVANCED_WRITE_TOOLS",
    "RETURN_ORDER_WRITE_TOOLS",
    "COMPANY_WRITE_TOOLS",
    # Part tools
    "create_part",
    "update_part",
    "deactivate_part",
    "duplicate_part",
    "set_part_parameter",
    # Stock tools
    "add_stock",
    "remove_stock",
    "transfer_stock",
    "count_stock",
    "merge_stock",
    # Advanced stock tools
    "serialize_stock",
    "install_stock",
    "uninstall_stock",
    "assign_stock",
    "return_stock",
    # Stock operation tools
    "change_stock_status",
    "convert_stock",
    "add_stock_test_result",
    "split_stock",
    "update_stock_location",
    # BOM tools
    "add_bom_item",
    "update_bom_item",
    "delete_bom_item",
    "add_bom_substitute",
    "validate_bom",
    # Purchase order tools
    "create_purchase_order",
    "add_po_line_item",
    "issue_purchase_order",
    "receive_po_items",
    "cancel_purchase_order",
    # Sales order tools
    "create_sales_order",
    "add_so_line_item",
    "issue_sales_order",
    "create_so_shipment",
    "allocate_so_stock",
    # Sales order advanced tools
    "ship_so_shipment",
    "cancel_sales_order",
    "hold_sales_order",
    "complete_sales_order",
    "update_sales_order",
    # Build order tools
    "create_build_order",
    "issue_build_order",
    "allocate_build_stock",
    "complete_build_output",
    "cancel_build_order",
    # Build order advanced tools
    "hold_build_order",
    "update_build_order",
    "unallocate_build_stock",
    "auto_allocate_build",
    "finish_build_order",
    # Return order tools
    "create_return_order",
    "add_ro_line_item",
    "issue_return_order",
    "receive_ro_items",
    "complete_return_order",
    # Company tools
    "create_company",
    "update_company",
    "create_supplier_part",
    "create_manufacturer_part",
    "create_company_contact",
    # Category tools
    "CATEGORY_WRITE_TOOLS",
    "create_part_category",
    "update_part_category",
    "create_stock_location",
    "delete_stock_location",
    # Attachment tools
    "ATTACHMENT_WRITE_TOOLS",
    "add_part_attachment",
    "delete_attachment",
    "add_stock_attachment",
    "print_label",
    "create_label_template",
    # Parameter tools
    "PARAMETER_WRITE_TOOLS",
    "create_parameter_template",
    "update_parameter_template",
    "delete_parameter_template",
    "bulk_set_parameters",
    "copy_parameters",
    # Test template tools
    "TEST_TEMPLATE_WRITE_TOOLS",
    "create_test_template",
    "update_test_template",
    "delete_test_template",
    "update_stock_test_result",
    "delete_stock_test_result",
    # Notification tools
    "NOTIFICATION_WRITE_TOOLS",
    "mark_notification_read",
    "mark_all_notifications_read",
    "delete_notification",
    "create_notification",
    "send_stock_alert",
    # Address tools
    "ADDRESS_WRITE_TOOLS",
    "create_company_address",
    "update_company_address",
    "delete_company_address",
    "update_company_contact",
    "delete_company_contact",
    # Project code tools
    "PROJECT_CODE_WRITE_TOOLS",
    "create_project_code",
    "update_project_code",
    "delete_project_code",
    "assign_project_code",
    "remove_project_code",
    # Settings tools
    "SETTINGS_WRITE_TOOLS",
    "update_global_setting",
    "update_user_setting",
    "create_custom_state",
    "update_custom_state",
    "delete_custom_state",
    # Supplier part tools
    "SUPPLIER_PART_WRITE_TOOLS",
    "update_supplier_part",
    "delete_supplier_part",
    "update_manufacturer_part",
    "delete_manufacturer_part",
    "add_supplier_price_break",
]
