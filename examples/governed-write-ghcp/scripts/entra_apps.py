#!/usr/bin/env python3
"""Create/reuse the three Entra API registrations for a governed-write foundation.

Idempotent: looks up apps by displayName. Uses `az rest` (Graph) with the
caller's existing az login and prints the IDs as JSON. Requires a tenant role
that can register apps. Workload app-role grants are a later, separate step
(after the hosted agent identity exists).

Optional human-review setup (explicit flags only):
* ``--review-client <appId>`` pre-authorizes that public client for the delegated
  ``Governance.Approve``/``Governance.Read`` scopes; list the same ID in
  ``human_clients``. ``threadlight-review-action --client-id`` uses it.
* ``--approver <objectId>`` assigns the ``Approver`` app role to that person.
"""
import argparse
import json
import subprocess
import uuid

GRAPH = "https://graph.microsoft.com/v1.0"


def az(*args, body=None):
    cmd = ["az", "rest", "--only-show-errors", *args]
    if body is not None:
        cmd += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else None


def role(value, members, description):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"threadlight/{value}/{'-'.join(members)}")),
            "allowedMemberTypes": members, "value": value, "displayName": value,
            "description": description, "isEnabled": True}


def scope(value, description):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"threadlight/scope/{value}")), "value": value,
            "type": "User", "isEnabled": True,
            "adminConsentDisplayName": description, "adminConsentDescription": description,
            "userConsentDisplayName": description, "userConsentDescription": description}


def ensure(name, roles, scopes=()):
    found = az("--method", "get", "--url",
               f"{GRAPH}/applications?$filter=displayName eq '{name}'&$select=id,appId")["value"]
    if found:
        app = found[0]
    else:
        app = az("--method", "post", "--url", f"{GRAPH}/applications",
                 body={"displayName": name, "signInAudience": "AzureADMyOrg"})
    # Workload tokens must carry idtyp=app (optional claim); Task8/9 reject app tokens without it.
    patch = {"identifierUris": [f"api://{app['appId']}"], "appRoles": roles,
             "optionalClaims": {"accessToken": [{"name": "idtyp", "essential": False, "additionalProperties": []}]},
             "api": {"requestedAccessTokenVersion": 2, "oauth2PermissionScopes": list(scopes)}}
    az("--method", "patch", "--url", f"{GRAPH}/applications/{app['id']}", body=patch)
    sps = az("--method", "get", "--url",
             f"{GRAPH}/servicePrincipals?$filter=appId eq '{app['appId']}'&$select=id")["value"]
    sp = sps[0] if sps else az("--method", "post", "--url", f"{GRAPH}/servicePrincipals",
                                body={"appId": app["appId"]})
    return {"appId": app["appId"], "objectId": app["id"], "servicePrincipalId": sp["id"]}


def review_setup(control, *, client=None, approver=None):
    app = az("--method", "get", "--url", f"{GRAPH}/applications/{control['objectId']}?$select=api,appRoles")
    scopes = {s["value"]: s["id"] for s in app["api"]["oauth2PermissionScopes"]}
    if client:
        az("--method", "patch", "--url", f"{GRAPH}/applications/{control['objectId']}", body={"api": {
            "preAuthorizedApplications": [{"appId": client, "delegatedPermissionIds": [
                scopes["Governance.Approve"], scopes["Governance.Read"]]}]}})
    if approver:
        role = next(r["id"] for r in app["appRoles"] if r["value"] == "Approver")
        sp = control["servicePrincipalId"]
        current = az("--method", "get", "--url", f"{GRAPH}/servicePrincipals/{sp}/appRoleAssignedTo")["value"]
        if not any(a["principalId"] == approver and a["appRoleId"] == role for a in current):
            az("--method", "post", "--url", f"{GRAPH}/servicePrincipals/{sp}/appRoleAssignedTo",
               body={"principalId": approver, "resourceId": sp, "appRoleId": role})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True, help="display-name prefix, e.g. tl-gov-demo")
    parser.add_argument("--review-client", help="public client appId pre-authorized for Governance.Approve")
    parser.add_argument("--approver", help="object ID of the human Approver")
    args = parser.parse_args()
    workload = role("Governance.Workload", ["Application"], "Allowed governance workload")
    result = {
        "control_plane": ensure(f"{args.prefix}-control", [
            workload,
            role("Approver", ["User"], "Human approver of governed actions"),
            role("Auditor", ["User"], "Human auditor of governance records"),
        ], [scope("Governance.Read", "Read governance records"),
            scope("Governance.Approve", "Decide governed action approvals")]),
        "gateway": ensure(f"{args.prefix}-gateway", [workload]),
        "business": ensure(f"{args.prefix}-business", [workload]),
    }
    if args.review_client or args.approver:
        review_setup(result["control_plane"], client=args.review_client, approver=args.approver)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
