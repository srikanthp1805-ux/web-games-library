# 🎮 Web Games Library

A personal collection of browser-based games — no installation, no login, just open and play.

🌐 **Live site:** [srikanthp1805-ux.github.io/web-games-library](https://srikanthp1805-ux.github.io/web-games-library)

---

## Available Games

### ♟ Chess
Full-featured chess with all standard rules (castling, en passant, pawn promotion, check/checkmate/stalemate).

| Mode | Description |
|------|-------------|
| [vs Computer](games/chess/solo.html) | Play against an AI with 4 difficulty levels (Easy → Expert). Uses minimax algorithm with alpha-beta pruning. |
| [2-Player Online](games/chess/multiplayer.html) | Challenge a friend in real-time via peer-to-peer connection. One player hosts and shares a 6-letter room code. Includes in-game chat, draw offers, and resign. |

---

## Coming Soon
- ⭕ **Tic Tac Toe** — Classic + extended 5×5, vs AI or friend
- 🔤 **Wordle** — Daily challenge + unlimited free play
- 🐍 **Snake** — Classic arcade with speed levels

---

## Tech Stack
- Pure HTML, CSS, JavaScript — zero dependencies for most games
- [PeerJS](https://peerjs.com/) for WebRTC peer-to-peer in multiplayer games

## How to Run Locally
Just open any `.html` file directly in your browser — no build step needed.
