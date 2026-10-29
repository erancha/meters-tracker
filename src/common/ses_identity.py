"""What SES knows about a recipient address while the account sits in the SES sandbox, where a
message is delivered only to an address that is itself a verified identity."""

# SES's own status words for an identity it has been asked about: the address confirmed the
# request, or the request is out and awaiting the click. Any other status means asking again.
VERIFIED = "Success"
PENDING = "Pending"


def verification_status(ses, email):
    """SES's verification status for the address — VERIFIED, PENDING, "Failed" and the like — or
    None for an address SES has never been asked about, which it leaves out of its answer."""
    known = ses.get_identity_verification_attributes(Identities=[email])["VerificationAttributes"]
    return known[email]["VerificationStatus"] if email in known else None
