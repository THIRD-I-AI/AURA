import { AuditFrontDoor } from '../audit/AuditFrontDoor';

/**
 * First-class in-app Audit Service tab. Promotes the signed-certificate flow
 * out of the account menu and into the Certificates section of the sidebar,
 * so a logged-in auditor reaches it like any other workspace surface. Reuses
 * the public front door embedded (its own auth nav is suppressed — the
 * dashboard header already carries the account menu).
 */
export default function AuditService() {
  return <AuditFrontDoor embedded />;
}
