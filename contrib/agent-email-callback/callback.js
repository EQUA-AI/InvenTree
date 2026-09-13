// This page neither redeems nor persists authorization codes. The initiating
// AIMMS session validates state and PKCE when the user submits the callback.
const parameters = new URLSearchParams(window.location.search);
if (window.location.pathname === '/microsoft/callback') {
  const heading = document.getElementById('heading');
  const message = document.getElementById('message');
  if (parameters.has('error')) {
    heading.textContent = 'Connection not completed';
    message.textContent =
      'Microsoft did not complete authorization. Return to AIMMS and try again. Your organisation may require administrator approval.';
  } else if (
    parameters.get('code') &&
    parameters.get('state') &&
    parameters.getAll('code').length === 1 &&
    parameters.getAll('state').length === 1
  ) {
    heading.textContent = 'Finish connecting in AIMMS';
    message.textContent =
      'Microsoft returned an authorization response. Your mailbox is not connected until AIMMS validates it.';
    document.getElementById('completion').hidden = false;
    document.getElementById('copy').addEventListener('click', async () => {
      const status = document.getElementById('status');
      try {
        await navigator.clipboard.writeText(window.location.href);
        status.textContent = 'Copied. Return to AIMMS to finish connecting.';
      } catch {
        status.textContent =
          "Copy this page's address from your browser, then return to AIMMS.";
      }
    });
  } else {
    heading.textContent = 'Start from AIMMS';
    message.textContent =
      'This page needs a Microsoft authorization response. Start a new connection from the Agent mailboxes panel in AIMMS.';
  }
}
