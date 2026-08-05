"""Ruleset definitions which control the InvenTree user permissions."""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from generic.enums import StringEnum


class RuleSetEnum(StringEnum):
    """Enumeration of ruleset names."""

    ADMIN = 'admin'
    PART_CATEGORY = 'part_category'
    PART = 'part'
    BOM = 'bom'
    STOCK_LOCATION = 'stock_location'
    STOCK = 'stock'
    BUILD = 'build'
    PURCHASE_ORDER = 'purchase_order'
    SALES_ORDER = 'sales_order'
    RETURN_ORDER = 'return_order'
    TRANSFER_ORDER = 'transfer_order'
    WORK_ORDER = 'work_order'


# This is a list of all the ruleset choices available in the system.
# These are used to determine the permissions available to a group of users.
RULESET_CHOICES = [
    (RuleSetEnum.ADMIN, _('Admin')),
    (RuleSetEnum.PART_CATEGORY, _('Part Categories')),
    (RuleSetEnum.PART, _('Parts')),
    (RuleSetEnum.BOM, _('Bills of Material')),
    (RuleSetEnum.STOCK_LOCATION, _('Stock Locations')),
    (RuleSetEnum.STOCK, _('Stock Items')),
    (RuleSetEnum.BUILD, _('Build Orders')),
    (RuleSetEnum.PURCHASE_ORDER, _('Purchase Orders')),
    (RuleSetEnum.SALES_ORDER, _('Sales Orders')),
    (RuleSetEnum.RETURN_ORDER, _('Return Orders')),
    (RuleSetEnum.TRANSFER_ORDER, _('Transfer Orders')),
    (RuleSetEnum.WORK_ORDER, _('Work Orders')),
]

# Ruleset names available in the system.
RULESET_NAMES = [choice[0] for choice in RULESET_CHOICES]

# Permission types available for each ruleset.
RULESET_PERMISSIONS = ['view', 'add', 'change', 'delete']

RULESET_CHANGE_INHERIT = [('part', 'bomitem')]


# Named action permissions which do not fit the standard model CRUD columns.
RULESET_CUSTOM_PERMISSIONS = {
    RuleSetEnum.WORK_ORDER: {
        'can_capture_closeout': ('tasks_closeoutcapture', 'capture_closeout'),
        'can_review_closeout': ('tasks_closeoutcapture', 'review_closeout'),
        'can_reconcile_closeout_parts': (
            'tasks_closeoutcapture',
            'reconcile_closeout_parts',
        ),
        'can_verify_closeout': ('tasks_closeoutcapture', 'verify_closeout'),
        'can_amend_closeout': ('tasks_closeoutcapture', 'amend_closeout'),
        'can_view_closeout_audit': ('tasks_closeoutcapture', 'view_closeout_audit'),
        'can_plan_workorder': ('tasks_workorder', 'plan_workorder'),
        'can_assign_workorder': ('tasks_workorder', 'assign_workorder'),
        'can_transition_workorder': ('tasks_workorder', 'transition_workorder'),
        'can_execute_workorder': ('tasks_workorder', 'execute_workorder'),
        'can_complete_workorder': ('tasks_workorder', 'complete_workorder'),
        'can_view_workorder_audit': ('tasks_workorder', 'view_workorder_audit'),
        'can_author_procedure': ('tasks_procedure', 'author_procedure'),
        'can_review_procedure': ('tasks_procedure', 'review_procedure'),
        'can_publish_procedure': ('tasks_procedure', 'publish_procedure'),
        'can_apply_procedure': ('tasks_procedure', 'apply_procedure'),
        'can_manage_jobkit': ('tasks_jobkit', 'manage_jobkit'),
        'can_reserve_jobkit': ('tasks_jobkit', 'reserve_jobkit'),
        'can_stage_jobkit': ('tasks_jobkit', 'stage_jobkit'),
        'can_issue_jobkit': ('tasks_jobkit', 'issue_jobkit'),
        'can_approve_jobkit_substitution': (
            'tasks_jobkit',
            'approve_jobkit_substitution',
        ),
    }
}


def get_ruleset_models() -> dict:
    """Return a dictionary of models associated with each ruleset.

    This function maps particular database models to each ruleset.
    """
    ruleset_models = {
        RuleSetEnum.ADMIN: [
            'auth_group',
            'auth_user',
            'auth_permission',
            'users_apitoken',
            'users_ruleset',
            'report_labeltemplate',
            'report_reportasset',
            'report_reportsnippet',
            'report_reporttemplate',
            'account_emailaddress',
            'account_emailconfirmation',
            'socialaccount_socialaccount',
            'socialaccount_socialapp',
            'socialaccount_socialtoken',
            'otp_totp_totpdevice',
            'otp_static_statictoken',
            'otp_static_staticdevice',
            'mfa_authenticator',
            # Oauth
            'oauth2_provider_application',
            'oauth2_provider_grant',
            'oauth2_provider_idtoken',
            'oauth2_provider_accesstoken',
            'oauth2_provider_refreshtoken',
            'oauth2_provider_devicegrant',
            # Plugins
            'plugin_pluginconfig',
            'plugin_pluginsetting',
            'plugin_pluginusersetting',
            # Misc
            'common_barcodescanresult',
            'common_newsfeedentry',
            'taggit_tag',
            'taggit_taggeditem',
            'flags_flagstate',
            'machine_machineconfig',
            'machine_machinesetting',
            # common / comms
            'common_emailmessage',
            'common_emailthread',
            'django_mailbox_mailbox',
            'django_mailbox_messageattachment',
            'django_mailbox_message',
        ],
        RuleSetEnum.BOM: ['part_bomitem', 'part_bomitemsubstitute'],
        RuleSetEnum.BUILD: [
            'part_part',
            'part_partcategory',
            'part_bomitem',
            'part_bomitemsubstitute',
            'build_build',
            'build_builditem',
            'build_buildline',
            'stock_stockitem',
            'stock_stocklocation',
        ],
        RuleSetEnum.PART_CATEGORY: [
            'part_partcategory',
            'part_partcategoryparametertemplate',
            'part_partcategorystar',
        ],
        RuleSetEnum.PART: [
            'part_part',
            'part_partpricing',
            'part_partsellpricebreak',
            'part_partinternalpricebreak',
            'part_parttesttemplate',
            'part_partrelated',
            'part_partstar',
            'part_partstocktake',
            'part_partcategorystar',
            'company_supplierpart',
            'company_manufacturerpart',
        ],
        RuleSetEnum.STOCK_LOCATION: ['stock_stocklocation', 'stock_stocklocationtype'],
        RuleSetEnum.STOCK: [
            'stock_stockitem',
            'stock_stockitemtracking',
            'stock_stockitemtestresult',
        ],
        RuleSetEnum.PURCHASE_ORDER: [
            'company_company',
            'company_contact',
            'company_address',
            'company_manufacturerpart',
            'company_supplierpart',
            'company_supplierpricebreak',
            'order_purchaseorder',
            'order_purchaseorderlineitem',
            'order_purchaseorderextraline',
        ],
        RuleSetEnum.SALES_ORDER: [
            'company_company',
            'company_contact',
            'company_address',
            'order_salesorder',
            'order_salesorderallocation',
            'order_salesorderlineitem',
            'order_salesorderextraline',
            'order_salesordershipment',
        ],
        RuleSetEnum.RETURN_ORDER: [
            'company_company',
            'company_contact',
            'company_address',
            'order_returnorder',
            'order_returnorderlineitem',
            'order_returnorderextraline',
        ],
        RuleSetEnum.TRANSFER_ORDER: [
            'order_transferorder',
            'order_transferorderallocation',
            'order_transferorderlineitem',
        ],
        # Maintenance work orders (the Kanban board) and the equipment assets they
        # are performed against. Before this ruleset existed, these endpoints
        # guarded only with IsAuthenticatedOrReadScope -- any authenticated user
        # could create, edit, move and archive any card, on any customer's machine.
        #
        # 'change' is what gates schedule editing: dragging a bar on the calendar
        # or timeline is a change to the work order, not a distinct capability.
        RuleSetEnum.WORK_ORDER: [
            'tasks_workorder',
            'tasks_workorderpart',
            # A card is how a work order's tracked work appears on the board,
            # so moving one exercises work-order authority, not a separate one.
            'tasks_kanbancard',
            'assets_assetmachine',
            'assets_assetmaintenancerecord',
            'assets_machinepart',
        ],
    }

    if settings.SITE_MULTI:
        ruleset_models['admin'].append('sites_site')

    return ruleset_models


def get_ruleset_ignore() -> list[str]:
    """Return a list of database tables which do not require permissions."""
    return [
        # Core django models (not user configurable)
        'admin_logentry',
        'contenttypes_contenttype',
        # Models which currently do not require permissions
        'common_attachment',
        'common_parametertemplate',
        'common_parameter',
        'common_customunit',
        'common_dataoutput',
        'common_inventreesetting',
        'common_inventreeusersetting',
        'common_notificationentry',
        'common_notificationmessage',
        'common_notesimage',
        'common_projectcode',
        'common_webhookendpoint',
        'common_webhookmessage',
        'common_inventreecustomuserstatemodel',
        'common_selectionlistentry',
        'common_selectionlist',
        'users_owner',
        'users_userprofile',  # User profile is handled in the serializer - only own user can change
        # Third-party tables
        'error_report_error',
        'exchange_rate',
        'exchange_exchangebackend',
        'usersessions_usersession',
        'sessions_session',
        # Django-q
        'django_q_ormq',
        'django_q_failure',
        'django_q_task',
        'django_q_schedule',
        'django_q_success',
        # Importing
        'importer_dataimportsession',
        'importer_dataimportcolumnmap',
        'importer_dataimportrow',
        # EQUA fork models - these apps enforce their own endpoint-level
        # permissions (e.g. approvals.review, work_order RBAC role checks,
        # scoped-conversation grants) instead of the generic ruleset table
        'aichat_chatactionproposal',
        'aichat_chatcitation',
        'aichat_chatmessage',
        'aichat_chatthread',
        'aichat_chattoolinvocation',
        'aichat_chatturn',
        'aichat_scopedconversation',
        'aichat_scopedconversationgrant',
        'approvals_approval',
        'approvals_approvalevent',
        'approvals_approvalrevision',
        'approvals_executedeffect',
        'part_partcandidateevaluation',
        'part_partverificationcommand',
        'part_partverificationdecision',
        'part_partverificationevent',
        'part_partverificationevidence',
        'part_partverificationpolicyversion',
        'part_partverificationrequirement',
        'part_partverificationsession',
        'part_partverificationuse',
        'repair_lockoutpoint',
        'repair_repairpacket',
        'repair_repairpacketapprovallink',
        'repair_repairpacketevent',
        'repair_repairpacketevidence',
        'repair_repairpacketgate',
        'repair_repairpacketgenerationrun',
        'repair_riskactionlink',
        'repair_riskfinding',
        'repair_riskfindingevent',
        'repair_risknotificationdelivery',
        'repair_riskruleconfigurationevent',
        'repair_riskruledefinition',
        'repair_riskscancandidate',
        'repair_riskscanlease',
        'repair_riskscanrun',
        'repair_safetyevidenceproof',
        'repair_safetygatetemplate',
        'tasks_closeoutamendment',
        'tasks_closeoutcapture',
        'tasks_closeoutcapturerevision',
        'tasks_closeouteffect',
        'tasks_closeoutfielddecision',
        'tasks_closeoutlearningdraft',
        'tasks_closeoutpartusage',
        'tasks_closeoutproposal',
        'tasks_closeoutreading',
        'tasks_closeoutreadingevidence',
        'tasks_jobkit',
        'tasks_jobkitallocation',
        'tasks_jobkitline',
        'tasks_jobkitshortage',
        'tasks_jobkitsubstitution',
        'tasks_workorderdependency',
        'tasks_kanbancolumn',
        'tasks_procedure',
        'tasks_procedureapplicability',
        'tasks_procedurefielddecision',
        'tasks_procedureresourcerequirement',
        'tasks_procedurerevision',
        'tasks_procedurerevisionsource',
        'tasks_procedurestep',
        'tasks_workingcalendar',
        'tasks_workordercloseout',
        'tasks_workordercommand',
        'tasks_workorderdeletionrecord',
        'tasks_workorderdeviation',
        'tasks_workorderevent',
        'tasks_workorderprocedureapplication',
        'tasks_workorderstepexecution',
        'voice_voicecapturesession',
        'voice_voicesession',
        'voice_voicetranscriptacceptance',
        'voice_voicetranscriptrevision',
        'voice_voicetransportattempt',
        'voice_voiceutterance',
    ]
