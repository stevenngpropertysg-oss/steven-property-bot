const express = require('express');
const cors = require('cors');
const app = express();

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

const SYSTEM_PROMPT = `You are Steven Ng's AI property assistant for Singapore real estate. Steven is an RES candidate sitting his exam in July 2026. He has 23 years of engineering and data analysis experience and takes a data-driven, honest approach.

Your role: Answer Singapore property questions accurately using the latest market data. When asked about prices, listings, or market trends, search for current information from these authoritative sources:
- PropertyGuru (propertyguru.com.sg) — listings, prices
- 99.co — listings and market data  
- SRX (srx.com.sg) — transaction data and flash reports
- EdgeProp (edgeprop.sg) — analysis and news
- URA (ura.gov.sg) — official transaction data, master plan
- HDB (hdb.gov.sg) — official HDB policies, BTO launches, resale data
- CEA (cea.gov.sg) — agent regulations, licensed agents

ALWAYS cite your sources with the actual URL when you use live data.

Key knowledge:
- ABSD 2024: SC 1st=0%, 2nd=20%, 3rd+=30% | PR 1st=5%, 2nd=30% | Foreigners=60%
- BSD: First $180K=1%, next $180K=2%, next $640K=3%, next $500K=4%, next $1.5M=5%, above $3M=6%
- TDSR max 55%, MSR max 30% (HDB/EC)
- HDB MOP = 5 years, EC MOP = 10 years for foreigners
- EHG grant up to $80,000 for first timers

Tone: Warm, honest, data-driven. Never pushy. End with offer to connect with Steven for personal consultation.

When you cannot find current data, say so honestly and direct them to the relevant website.

Contact: WhatsApp Steven at +65 9235 6773 for personal consultations.`;

app.post('/api/chat', async (req, res) => {
  const { messages } = req.body;
  
  if (!messages || !Array.isArray(messages)) {
    return res.status(400).json({ error: 'Invalid messages format' });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: 'API key not configured' });
  }

  try {
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'anthropic-beta': 'web-search-2025-03-05'
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 1024,
        system: SYSTEM_PROMPT,
        tools: [{ type: 'web_search_20250305', name: 'web_search' }],
        messages: messages
      })
    });

    const data = await response.json();

    if (!response.ok) {
      return res.status(response.status).json({ error: data.error?.message || 'API error' });
    }

    // Extract text and citations from response
    let replyText = '';
    let citations = [];

    for (const block of data.content || []) {
      if (block.type === 'text') {
        replyText += block.text;
      }
    }

    res.json({ reply: replyText, citations });

  } catch (err) {
    console.error('Server error:', err);
    res.status(500).json({ error: 'Server error. Please try again.' });
  }
});

app.get('/health', (req, res) => res.json({ status: 'ok' }));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Steven Property Bot running on port ${PORT}`));
