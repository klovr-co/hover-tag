# ADR 0013: One Slack app per organization through a hub

- Status: Proposed
- Date: 2026-10-25
- Related: [ADR 0001](0001-credential-boundary.md),
  [ADR 0005](0005-multiple-tag-instances.md),
  ADR 0010 channel watching (`feat/decision-classifier-auto-invoke`),
  [ADR 0011](0011-chief-of-tag.md),
  [ADR 0012](0012-one-slack-app-with-tag-profiles.md)
- Extends ADR 0012 from one installation to every installation in an
  organization.

## Context

ADR 0012 lets one installation's Tags share one Slack app as profiles, with
Chief-of-Tag's bridge holding the app's connection. It stops at the machine
boundary: in an organization where many people each run Tags, every person
still has their own app, and Slack's App Directory and channel member lists
fill up with near-identical "Tag" apps.

Extending ADR 0012 across machines is limited by Slack's Socket Mode, which
delivers each event to one of an app's open connections, chosen by Slack.
Tags on different machines cannot each hold a connection to a shared app, so
one process must own the connection and route to the others.

## Decision

**One app per organization, owned by a hub.** The hub is one distributable
service. We run a cloud hub; an organization may run its own. A Slack app
belongs to exactly one hub, and only the hub holds its `xapp-` and `xoxb-`
tokens. A self-hosted hub must stay online for its Tags to answer.

**Every Tag in hub mode is an ADR 0012 profile whose owner is a person.**
Name parsing (`@Tag designer: …`), thread owners, the `profile-messages`
record, profile-attributed approvals, and posting with `username` and
`icon_url` work as ADR 0012 describes, with the hub in the role ADR 0012 gives
Chief-of-Tag's bridge. Display names include the owner, for example
*Research Tag (Maxine)*.

**Local Tags dial out.** Each Tag runs its backend on its owner's machine. Its
bridge opens an authenticated outbound connection to the hub in place of a
Socket Mode connection, receives routed events on it, and sends replies back.
Each Tag stores the URL of its hub, so one machine can run Tags on different
hubs, alongside app Tags from ADR 0005.

**The hub holds the only bot token.** Unlike ADR 0012, where profiles inherit
Chief-of-Tag's token, hub-mode machines never receive it. Slack calls that
helpers make directly today (thread reads, history search, canvases, files)
become hub operations scoped to the Tag's workspace, its allowed channels and
the invoking thread. This is the credential broker anticipated in ADR 0001,
and it is required here because machines belong to different people.

**Owner sign-in.** Owners connect a machine with Sign in with Slack on the
organization's app. The hub maps the sign-in to the owner's member ID and
issues a per-machine key, kept in the system keychain, that all of that
owner's Tags on the machine use. Owners and admins can revoke a machine, and
the hub revokes keys when Slack deactivates the member. Machines without a
browser pair with a one-time code from `/tag pair`.

**Every person has a Chief-of-Tag.** A person must set up their own
Chief-of-Tag, the ADR 0011 role held per person rather than per
installation, before the hub accepts their requests or their Tags. It runs on
its owner's machine like their other Tags and is their default profile. In
Slack it posts as *Tag (Owner)*, for example *Tag (Maxine)*; "Chief-of-Tag" is
the role's name in setup, Tag.app and documentation.

**Sharing.** Each owner decides which of their Tags are shared with the
organization and which stay private. A Chief-of-Tag may consult its owner's
Tags and any Tag shared with its owner. The hub checks that permission, and
the Tag's allowed users and channels, on every consultation.

**Routing.** The hub sends every request to the sender's Chief-of-Tag,
unless the message names a Tag (ADR 0012 name parsing) or continues a thread
another Tag owns. The Chief-of-Tag decides which Tags to consult, up to a
hub setting (3 by default), and writes one combined answer that names the
Tags it consulted.

- This deliberately changes ADR 0011, which never combines answers. The
  Chief-of-Tag is the single owner of the request and the combined answer,
  and consulted Tags answer it, not the thread. ADR 0011's hop limit stays:
  a consulted Tag cannot consult further.
- A consulted shared Tag runs on its owner's machine with its owner's AI
  account, files and memory. Its owner pays for that run.
- ADR 0010's classifier (TypeSafe Jev) gives the Chief-of-Tag a first guess
  at relevant Tags: one call asks one yes/no question per Tag the sender may
  use, about the same thread. The Chief-of-Tag's own model decides from
  there.

**Correcting the choice.** Under each combined answer, buttons offer
**＋ Get *Tag*'s view too** for the next most likely Tags, which reruns the
answer with that Tag's input added, and **↻ Redo with a different Tag**,
a menu of every Tag the clicker may use, which discards the answer and starts
again with the chosen Tag. Anyone in the thread who may use that Tag can
click; the hub checks again on click. Each click is recorded as a label for
tuning the classifier, as ADR 0010 describes.

**Channel watching.** ADR 0010's gates move to the hub, which sees every
event once. That removes ADR 0010's caveat that several Tags in one channel
can each answer the same message: the hub sends a watched message to at most
the Tags routing selects. Watched runs remain text-only untrusted sessions.

**Content through the cloud hub.** Routed message content may pass through
our cloud hub. Organizations that do not accept this run their own hub.

**DMs and App Home.** A DM to the shared app goes to the sender's
Chief-of-Tag. App Home lists the sender's Tags and the Tags shared with them.

**Offline Tags.** If a selected Tag has no live connection, the hub replies
that it is offline and does not queue the request.

## Open questions

1. The classifier has only been measured on whether a message is addressed to
   Tag, not on choosing between Tags. A small labelled set of messages and the
   Tags that should answer them must be scored before it guides consultation.
2. Whether a shared Tag's owner can cap what others' consultations cost them.

## Consequences

- One app and one admin approval per organization; adding a Tag is a hub
  registration, not a Slack app creation.
- We operate a hosted service that must be secured and kept available; hub
  downtime stops every hub-mode Tag in the organization.
- The hub sees routed message content and holds the organization bot token,
  which moves the trust boundary from each owner's machine to the hub
  operator.
- Hub-mode backends lose direct Slack credentials, which is stronger than both
  ADR 0001 and ADR 0012.
- Nobody can use hub-mode Tags without first setting up a Chief-of-Tag.
- Combining answers reintroduces the error and cost risk ADR 0011 avoided;
  limiting consultation to 3 Tags and one hop bounds it.
- ADR 0012's trade-offs still apply: no @mention per Tag, and Slack surfaces
  outside messages show the shared app.
