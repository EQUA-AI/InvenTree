# Voice action eligibility

Generated from `ai/core/voice/action_policy.py` (voice-action-policy-v1). Do not edit by hand: regenerate with
`python manage.py shell -c "from ai.core.voice.action_policy import render_markdown; print(render_markdown(), end='')" > docs/docs/aimms/voice-action-eligibility.md`. A test fails when this file drifts.

Every business write requires a read-back and an explicit spoken confirmation (owner decision, 2026-09-09). *Confirmation* is the exact strict phrase for irreversible/external actions or a short assent for reversible ones. *Voice execution* is enforced by `FEATURE_VOICE_ACTION_POLICY_ENFORCE`; an unlisted action is always unavailable.

| Rail | Action | Severity | Confirmation | Change label | Review | Voice review | Voice execution | Reason / follow-up |
|---|---|---|---|---|---|---|---|---|
| proposal | `work_order.hold` | reversible | short assent | is now on hold | brief | yes | yes | — |
| proposal | `work_order.resume` | reversible | short assent | is now in progress | brief | yes | yes | — |
| proposal | `work_order.schedule` | reversible | short assent | has been scheduled | brief | yes | yes | — |
| proposal | `work_order.resize` | reversible | short assent | has been resized | brief | yes | yes | — |
| proposal | `work_order.update` | reversible | short assent | plan has been updated | brief | yes | yes | — |
| proposal | `work_order.assign` | reversible | short assent | has been assigned | brief | yes | yes | — |
| proposal | `work_order.transition` | reversible | short assent | has moved to the requested state | brief | yes | yes | — |
| proposal | `work_order.create_child` | reversible | short assent | now has the new child work order | brief | yes | yes | — |
| proposal | `work_order.generate_procurement` | reversible | short assent | now has the procurement child | brief | yes | yes | — |
| proposal | `dependency.create` | reversible | short assent | now has the new dependency | brief | yes | yes | — |
| proposal | `dependency.delete` | reversible | short assent | no longer has that dependency | brief | yes | yes | — |
| proposal | `repair_work_package.create` | reversible | short assent | repair work package has been created | full | yes | yes | — |
| proposal | `work_order.delete` | irreversible | `confirm delete` | has been deleted | full | yes | yes | — |
| proposal | `work_order.cancel` | irreversible | `confirm cancel` | is now cancelled | full | yes | yes | — |
| proposal | `work_order.create` | reversible | short assent | has been created | brief | yes | no | creating work orders by voice needs a screen preview (F-WO-2) |
| proposal | `schedule.optimize` | irreversible | `confirm optimize schedule` | schedule has been optimized | full | yes | no | batch scheduling must be reviewed on screen (F-WO-2) |
| tool | `add_bom_item` | reversible | short assent | now has the new bill-of-materials line | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `add_po_line_item` | reversible | short assent | now has the new order line | brief | yes | no | that action has no voice receipt yet (C7) |
| tool | `add_so_line_item` | reversible | short assent | now has the new sales order line | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `add_stock` | reversible | short assent | now has the added stock | brief | yes | no | that action has no voice receipt yet (E5) |
| tool | `add_stock_test_result` | reversible | short assent | now has the recorded test result | brief | yes | no | that stock action is not available by voice yet (E6) |
| tool | `assign_stock` | reversible | short assent | is now assigned | brief | yes | no | that stock action is not available by voice yet (E6) |
| tool | `cancel_purchase_order` | irreversible | `confirm cancel order` | is now cancelled | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `change_stock_status` | reversible | short assent | now has the new stock status | brief | yes | no | that stock action is not available by voice yet (E6) |
| tool | `complete_purchase_order` | irreversible | `confirm complete order` | is now complete | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `convert_stock` | irreversible | `confirm convert` | has been converted | full | yes | no | that stock action is not available by voice yet (E6) |
| tool | `count_stock` | reversible | short assent | now has the counted quantity | brief | yes | no | that action has no voice receipt yet (E5) |
| tool | `create_company` | reversible | short assent | has been created | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `create_manufacturer_part` | reversible | short assent | has been created | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `create_part` | reversible | short assent | has been created | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `create_part_category` | reversible | short assent | has been created | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `create_purchase_order` | reversible | short assent | has been created as a draft | brief | yes | no | that action has no voice receipt yet (C7) |
| tool | `create_sales_order` | reversible | short assent | has been created as a draft | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `create_stock_location` | reversible | short assent | has been created | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `create_supplier_part` | reversible | short assent | has been created | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `deactivate_part` | irreversible | `confirm deactivate` | is now inactive | full | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `delete_po_line_item` | irreversible | `confirm delete` | no longer has that order line | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `delete_purchase_order` | irreversible | `confirm delete` | has been deleted | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `generate_and_send_document` | external_effect | `confirm send` | document has been sent | full | no | no | the document must be checked on screen before sending (OD-15) |
| tool | `install_stock` | reversible | short assent | is now installed | brief | yes | no | that stock action is not available by voice yet (E6) |
| tool | `issue_purchase_order` | irreversible | `confirm issue order` | has been issued to the supplier | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `mark_email_processed` | external_effect | `confirm mark processed` | email is now marked processed | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `merge_stock` | irreversible | `confirm merge` | has been merged | full | yes | no | that stock action is not available by voice yet (E6) |
| tool | `receive_po_items` | irreversible | `confirm receive` | has been received | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `remove_stock` | irreversible | `confirm remove` | has had the stock removed | full | yes | no | that action has no voice receipt yet (E5) |
| tool | `return_stock` | irreversible | `confirm return` | has been returned | full | yes | no | that stock action is not available by voice yet (E6) |
| tool | `send_email` | external_effect | `confirm send` | email has been sent | full | yes | no | that action has no voice receipt yet (C7) |
| tool | `serialize_stock` | irreversible | `confirm serialize` | has been serialized | full | yes | no | that stock action is not available by voice yet (E6) |
| tool | `set_part_parameter` | reversible | short assent | now has the updated parameter | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `split_stock` | irreversible | `confirm split` | has been split | full | yes | no | that stock action is not available by voice yet (E6) |
| tool | `transfer_stock` | reversible | short assent | has been moved | brief | yes | no | that action has no voice receipt yet (E5) |
| tool | `uninstall_stock` | reversible | short assent | is now uninstalled | brief | yes | no | that stock action is not available by voice yet (E6) |
| tool | `update_part` | reversible | short assent | has been updated | brief | yes | no | that change is not available by voice yet (F-OTHER) |
| tool | `update_purchase_order` | reversible | short assent | has been updated | brief | yes | no | that action has no voice receipt yet (C7) |
| tool | `update_stock_location` | reversible | short assent | has been updated | brief | yes | no | that stock action is not available by voice yet (E6) |
