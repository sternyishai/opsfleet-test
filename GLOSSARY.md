# Retail Analysis Assistant

A chat assistant that lets the Retail Company's non-technical leadership ask analysis questions about sales and performance in natural language, and save the resulting reports.

## Language

### People

**User**:
A non-technical person who chats with the assistant, either an Executive or a Manager.
_Avoid_: Customer, account

**Executive**:
A C-level User, such as the CEO, who may analyze all Brands.
_Avoid_: Admin, leadership

**Manager**:
A User who is responsible for one or more Brands and may analyze only those Brands.
_Avoid_: Brand owner

**Customer**:
A shopper whose purchases appear in the transaction data (a row of the `users` table).
_Avoid_: User, shopper, buyer

### Data access

**Brand Assignment**:
The link between a Manager and one Brand they may analyze. A Manager can have several.
_Avoid_: Ownership, partition

### Conversations

**Conversation**:
One chat thread between a User and the assistant on a single channel. A thread started on another channel is a different Conversation.
_Avoid_: Session, chat, thread

### Reports

**Saved Report**:
A question, the SQL that answered it and the answer, saved during a Conversation because the User explicitly asked for it. It is private to its Author.
_Avoid_: Report library entry, document

**Author**:
The User who saved a Saved Report, and the only User who may see, change or delete it.
_Avoid_: Owner, creator

**Deletion Plan**:
The exact list of a User's Saved Reports that a delete request matched, each with the reason it matched, shown to the User for confirmation before anything is deleted.
_Avoid_: Delete preview, candidates

### Knowledge

**Trio**:
A historical expert artifact made of a Question, the SQL Query that answered it, and the Analyst Report written from the result.
_Avoid_: Example, sample

**Golden Bucket**:
The store of curated Trios that the assistant uses to understand how analysts interpreted past questions.
_Avoid_: Knowledge base, data lake

**Trio Candidate**:
A proposed Trio derived from past interactions, waiting for an analyst to approve it into the Golden Bucket.
_Avoid_: Draft trio, suggestion

### Personalization

**User Profile**:
What the assistant has learned about how a User likes answers presented, such as format, depth and charts.
_Avoid_: Memory, settings

**Persona**:
The company-wide tone and report style the assistant writes in, set by leadership and changeable at any time.
_Avoid_: Prompt, system prompt, personality
