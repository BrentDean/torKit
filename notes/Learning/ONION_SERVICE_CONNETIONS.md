# Onion Service Connections
## How Tor Finds and Connects to an Onion Service

---

# Why This Matters

When we built TorKit's new `--publish-service` feature, we learned something important:

Publishing an onion service is **not** the same thing as opening a TCP socket.

There is no DNS lookup.

There is no IP address.

There is no direct TCP connection from the client to the server.

Instead, Tor builds an anonymous path through several stages before a single HTTP request ever reaches our web server.

Understanding these stages makes OnionShare's source code much easier to understand.

---

# Traditional Website

When you visit:

```
https://example.com
```

your computer does roughly this:

```
Browser
    │
DNS Lookup
    │
IP Address
    │
TCP Connection
    │
TLS
    │
HTTP Request
```

Eventually the web server receives:

```
GET /
```

---

# Onion Website

When you visit:

```
http://abcdefghijk....onion
```

there is **no DNS server**.

Instead Tor performs several additional steps.

```
Browser
    │
Descriptor Lookup
    │
Introduction
    │
Rendezvous
    │
Hidden Service
    │
Local Web Server
```

Only after all of those complete does our web server receive:

```
GET /
```

---

# Step 1 — Onion Address

Suppose we publish

```
abc123def456.onion
```

Unlike a normal hostname,

```
example.com
```

this is **not**

- a DNS name
- an IP address
- something your ISP knows about

Instead the onion address is derived from the onion service's public key.

Think of it as the service's permanent identity.

---

# Step 2 — Descriptor Lookup

Your Tor client now asks the Tor network:

> Where is the onion service that owns this address?

It does **not** ask DNS.

Instead it downloads something called the

> Onion Service Descriptor

Think of this as a phone book entry.

It does **NOT** contain

- IP address
- hostname
- ISP
- country

Instead it contains information like

- introduction points
- encryption keys
- protocol information

Diagram

```
Client
   │
   ▼
Directory Servers
   │
   ▼
Onion Service Descriptor
```

---

# Step 3 — Introduction Points

Inside the descriptor are several Tor relays called

Introduction Points.

```
Descriptor

Introduction Point A

Introduction Point B

Introduction Point C
```

These relays already know how to contact the onion service.

Importantly:

They do **not** know where the server actually lives.

They simply know how to relay an encrypted introduction request.

---

# Step 4 — Client Creates a Rendezvous Point

The client now randomly chooses another Tor relay.

This relay becomes the

> Rendezvous Point

```
Client

      │

      ▼

Rendezvous Relay
```

This relay is where both sides will eventually meet.

---

# Step 5 — Introduction

The client contacts one of the introduction points.

```
Client

      │

      ▼

Introduction Point
```

It says something like

> Tell the onion service to meet me at this rendezvous relay.

The introduction point forwards the encrypted message.

It never learns:

- who the client is
- where the server lives

---

# Step 6 — Onion Service Connects

The onion service receives the introduction request.

It now creates its own Tor circuit to the rendezvous relay.

```
TorKit

    │

    ▼

Rendezvous Relay
```

---

# Step 7 — Secure Connection Established

Now both sides are connected.

```
Browser
      \
       \
        Rendezvous Relay
       /
      /
TorKit
```

Neither side knows the other's IP address.

The rendezvous relay only forwards encrypted Tor cells.

---

# Step 8 — HTTP Finally Happens

Only now does Tor deliver the HTTP request.

```
GET /
```

Our Python web server finally logs

```
127.0.0.1 - - "GET / HTTP/1.1" 200
```

Everything before this point happened entirely inside Tor.

---

# Why Our Curl Test Timed Out

Earlier we observed

```
curl

↓

SOCKS5 connect ...

↓

timeout
```

while our Python server logged nothing.

That tells us the request never reached

```
127.0.0.1:8000
```

Instead it stalled somewhere earlier.

Possible stages include

```
Descriptor Lookup

↓

Introduction

↓

Rendezvous

↓

HTTP
```

Since no HTTP request arrived,

the failure occurred before our local web server.

---

# Relation to TorKit

Originally OnionShare assumed

```
Random Local Port

↓

One Onion Service

↓

Exit
```

Our first architectural change introduced

```
OnionPortMapping
```

Instead of exposing only OnionShare's own temporary web server,

we can now expose

```
127.0.0.1:8000

127.0.0.1:3000

127.0.0.1:22

127.0.0.1:1883
```

through onion services.

Importantly,

this changes only the **final destination** after Tor finishes all of its anonymous connection setup.

Everything involving

- descriptor lookup
- introduction points
- rendezvous

remains Tor's responsibility.

---

# Connection Diagram

```
Browser

    │

Descriptor Lookup

    │

Introduction Point

    │

Rendezvous Relay

    │

TorKit Onion Service

    │

127.0.0.1:8000

    │

Python HTTP Server
```

---

# Key Takeaways

• Onion addresses are **not DNS names**.

• Onion addresses are derived from public keys.

• Clients download an onion service descriptor instead of performing DNS.

• Introduction points help clients contact the onion service without revealing its location.

• A rendezvous relay connects both sides anonymously.

• Only after Tor completes all of this does the web server receive an HTTP request.

• Our new `OnionPortMapping` changes only where the onion service forwards traffic locally—it does **not** change how Tor locates or connects to the onion service.