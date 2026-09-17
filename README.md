# ownwords

**Own your words.** Open source toolkit for authors who want to own their words. Bi-directional WordPress sync, local Markdown editing, batch AI-assisted editorial operations, and dual publishing to both WordPress and static sites. Your content, your files, your control.

## Vision

This toolkit is for human authors who want to:

1. **Own their content as local markdown files** - not locked in WordPress
2. **Use AI assistants for editorial tasks** - batch updates, terminology changes, link fixes
3. **Maintain dual publishing** - to WordPress and static sites simultaneously

**This is explicitly NOT for**: AI content generation, "slop" production, or replacing human authorship.

## Use Cases

Without tooling like ownwords, batch content operations on WordPress sites require days, weeks, or months of manual labor — and the process is stressful and error-prone. This toolkit makes programmatic content management practical.

### Content Updates at Scale

- **Author bio updates** — When author information lives in article body content (not just metadata), update hundreds of articles when someone's title, company, or bio changes
- **Terminology standardization** — Rebrand a product name, update style guide conventions, or fix outdated terms across your entire archive
- **Corrections at scale** — Fix factual errors, update statistics, or revise outdated information across multiple articles
- **Legal/compliance updates** — Update disclosures, disclaimers, or required language across all relevant content

### SEO and Link Management

- **Internal link building** — Add cross-references between related articles for improved SEO and link authority
- **Canonical URL migration** — Update links when content moves between domains or URL structures change
- **Broken link repair** — Find and fix broken internal links across your content library
- **Domain migration** — Update all links when moving from one domain to another

### Content Organization

- **Front matter enrichment** — Batch-add categories, tags, or structured metadata to legacy content
- **Taxonomy cleanup** — Standardize category and tag usage across articles
- **Series organization** — Add series metadata to group related articles

### Multi-Platform Publishing

- **Dual publishing** — Maintain content on both WordPress and a static site (Cloudflare Pages, Netlify, GitHub Pages)
- **Content backup** — Keep version-controlled Markdown files as the source of truth, with WordPress as a publishing target
- **Platform migration** — Export content from WordPress to other CMS platforms or static site generators
- **Headless CMS architecture** — Use local Markdown files as a content source for multiple frontends (websites, mobile apps, etc.), with WordPress as one of many publishing targets

### AI-Assisted Editorial Operations

- **Batch editing with AI** — Use Claude, Cursor, or other AI assistants to make intelligent edits across your content library
- **Style consistency** — Have AI assistants enforce writing style, tone, or formatting conventions
- **Content enrichment** — Add summaries, metadata, or structured data to existing articles

The Agent API includes `dryRun` support for all batch operations, so you can preview changes before applying them.

## Features

- **Fetch**: Download WordPress articles as raw HTML
- **Convert**: Transform HTML to clean Markdown with YAML front matter
- **Verify**: Independent QA verification of conversion accuracy
- **Export**: Convert Markdown back to WordPress-ready HTML
- **Publish**: Push content directly to WordPress via REST API
- **Agent API**: Batch operations for AI-assisted editorial tasks

## Installation

Ownwords runs on Node.js 18 or later. Install the CLI with npm or Bun:

```bash
npm install -g ownwords
# or
bun add -g ownwords
```

Homebrew uses the same checksum-bound release archive:

```bash
brew install synthesisengineering/tap/ownwords
```

The versioned shell installer verifies its exact release archive before installing
under `~/.local/bin` and explicitly running optional dormant-core setup:

```bash
curl -fsSL https://github.com/synthesiswriting/ownwords/releases/download/v1.6.0/install.sh | sh
```

It requires Node.js 18+, Python 3.9+ and curl. Default dormant setup additionally
requires Git and Python 3.12–3.14. To install Ownwords while declining that
optional acquisition, use:

```bash
curl -fsSL https://github.com/synthesiswriting/ownwords/releases/download/v1.6.0/install.sh | sh -s -- --no-dormant-core
```

Use `--prefix /absolute/path` to choose another installation directory. Unknown
launchers, changed release files, permission drift and unrecognized recovery
records are preserved and cause failure. Earlier version directories remain.

To use Ownwords as a library, install it locally with `npm install ownwords`.
The published npm package, Bun package and direct release archive include the
locked production dependencies. Bun may still query the npm registry while
installing those exact dependency versions. Package manager installation runs no setup scripts and
does not configure agents, services, WordPress accounts or AI providers.

For a direct installation, download the versioned archive and `SHA256SUMS` from
[GitHub releases](https://github.com/synthesiswriting/ownwords/releases), verify
its SHA-256, then extract it. Run `node package/bin/ownwords.js --help` from the
extracted directory. The archive contains portable JavaScript and its production
dependencies; it is not a native executable. An AUR package is not published.

### Optional Synthesis setup

Ownwords' authoring commands work independently. Explicit setup stages shared
Synthesis assets outside agent discovery, without activating a workspace,
native plugin, hook or service:

```bash
ownwords setup
```

This optional operation requires Git and Python 3.12, 3.13 or 3.14. It uses the
checksum-inventoried thin launcher bundled with the Ownwords release and fetches
that launcher's exact core version and commit. It records release provenance
and consumes download bandwidth and disk space. It does not call an LLM or add
anything to a model's context.

To decline optional staging:

```bash
ownwords setup --no-dormant-core --json
```

The declined path requires Python 3.9 or later, performs no acquisition or state
writes, and preserves any earlier staged assets or independent installation.
Ordinary commands and package postinstall never invoke setup. Explicit
`ownwords synthesis activate --profile full` or
`ownwords synthesis activate --profile skills-only` can activate
verified staged assets through the shared lifecycle engine.

Setup does not read article files, WordPress credentials or AI API keys, and it
does not send content to WordPress or an AI provider. Core downloads contact
GitHub. Authoring commands that explicitly fetch, publish or call an AI provider
retain their documented network and credential behavior. There is no telemetry
or background updater in the setup integration.

The explicit bridge also exposes `ownwords synthesis status`, `doctor`, `repair`,
`update` and `deactivate`. Arguments are forwarded to the pinned core launcher,
and failures or termination signals propagate to the caller. Cancellation is forwarded to the running child process group and the caller waits for it to stop. The shell installer applies the same rule to downloads and optional setup. These lifecycle
commands can inspect or change the Synthesis installation only when you invoke
them; they do not run article, WordPress or AI operations.

See [distribution maintenance](docs/distribution.md) for exact release binding,
package checksums, consumer acceptance and publication steps.

## CLI Usage

### Fetch a WordPress Article

```bash
# Fetch via HTML scraping (works for any WordPress site)
ownwords fetch https://example.com/blog/2025/01/01/my-article/

# Fetch via REST API (for your own sites - richer metadata)
ownwords fetch https://myblog.com/2025/01/my-article/ --api
ownwords fetch my-article-slug --api --site=myblog

# Specify output path (HTML scraping mode)
ownwords fetch https://example.com/blog/2025/01/01/my-article/ ./raw/my-article.html
```

**Date-prefixed filenames (Jekyll/Hugo convention):**

By default, fetched files use date-prefixed filenames like `2025-01-01-my-article.md`, following Jekyll and Hugo conventions. This prevents filename collisions when articles share the same slug across different dates.

```bash
# REST API mode: date comes from API response
# Creates: 2025-01-01-my-article.md
ownwords fetch https://example.com/my-article/ --api

# Disable date prefix: creates my-article.md
ownwords fetch https://example.com/my-article/ --api --no-date-prefix

# HTML scraping mode: date extracted from URL if present
# URL with date (/2025/01/01/slug/) → 2025-01-01-my-article.md
# URL without date (/my-article/) → my-article.md (fallback)
ownwords fetch https://example.com/blog/2025/01/01/my-article/
```

**Note:** REST API mode (`--api`) is recommended as it gets the publication date directly from WordPress regardless of URL structure. HTML scraping mode can only extract dates from URLs that contain them.

**REST API mode benefits:**

- Full categories and tags (names, slugs, IDs)
- Author information
- Featured image URL and alt text
- Exact excerpt (not parsed from HTML)
- JSON sidecar file for future bi-directional sync
- Automatic image downloading with smart caching

**Image downloading:**

By default, `fetch --api` downloads all images to the local content directory and rewrites image URLs to use local relative paths (e.g., `./image.png`). This makes your local files self-sufficient.

```bash
# Default: download images locally
ownwords fetch https://example.com/my-article/ --api --hierarchical

# Skip image downloading (keep remote URLs)
ownwords fetch https://example.com/my-article/ --api --no-images
```

Image download features:
- **Smart caching**: Only downloads images that don't exist locally or have changed
- **Size deduplication**: When WordPress serves multiple sizes of the same image, picks the highest quality version
- **URL rewriting**: All image URLs in markdown (and featured_image in front matter) are rewritten to local paths
- **Sidecar tracking**: Creates `index.images.json` to track original URLs and local filenames

### Convert HTML to Markdown

```bash
# Basic conversion
ownwords convert ./raw/my-article.html

# With metadata options
ownwords convert ./raw/my-article.html ./content/articles/my-article.md \
  --category="Core Series" \
  --series-order=1
```

### Verify Conversion Quality

```bash
# Verify single file
ownwords verify ./raw/my-article.html ./content/articles/my-article.md

# Batch verify all conversions
ownwords verify --batch ./raw ./content/articles

# Verbose output
ownwords verify --verbose ./raw/my-article.html ./content/articles/my-article.md
```

### Batch Convert Multiple Articles

```bash
# From URLs file (HTML scraping)
ownwords batch urls.txt --verify

# From URLs file via REST API (richer metadata)
ownwords batch urls.txt --api --site=myblog

# Skip fetch (convert existing HTML only)
ownwords batch urls.txt --skip-fetch --verify
```

**URLs file format:**
```
# Comments start with #
https://example.com/blog/2025/01/01/first-article/
https://example.com/blog/2025/01/02/second-article/
```

## Library Usage

```javascript
const {
  fetchArticle,
  convertFile,
  verifyConversion,
  exportToWordPress,
  AgentAPI
} = require('ownwords');

// Fetch an article
const html = fetchArticle(
  'https://example.com/blog/2025/01/01/my-article/',
  './raw/my-article.html'
);

// Convert to Markdown
const result = convertFile('./raw/my-article.html', './content/my-article.md', {
  category: 'Core Series',
  slug: 'my-article'
});
console.log(`Converted: ${result.title} (${result.wordCount} words)`);

// Verify conversion
const verification = verifyConversion('./raw/my-article.html', './content/my-article.md');
if (verification.issues.length > 0) {
  console.error('Issues found:', verification.issues);
}

// Export back to WordPress HTML
const exported = exportToWordPress('./content/my-article.md', './export/my-article.html');
```

## Agent API for AI-Assisted Editing

The Agent API is designed for AI coding assistants (Claude, Cursor, etc.) to perform batch editorial tasks:

```javascript
const { AgentAPI } = require('ownwords');
const agent = new AgentAPI('./content/articles/', {
  rawDir: './raw'  // For verification
});

// List all articles
const articles = agent.listArticles();
console.log(`Found ${articles.length} articles`);

// Search for content
const matches = agent.search('synthesis coding');

// Batch find and replace
const changes = agent.findAndReplace({
  pattern: /Claude AI/g,
  replacement: 'Claude',
  dryRun: true  // Preview changes first
});
console.log(`Would update ${changes.length} files`);

// Actually apply changes
agent.findAndReplace({
  pattern: /Claude AI/g,
  replacement: 'Claude'
});

// Update links (domain migration)
agent.updateLinks({
  oldDomain: 'old-site.com',
  newDomain: 'new-site.com'
});

// Update front matter
agent.updateFrontMatter({
  updates: { author: 'Rajiv Pant' },
  filter: (article) => !article.author  // Only articles without author
});

// Validate all articles
const results = agent.validateAll();
console.log(`${results.passed} of ${results.total} passed`);
```

## Front Matter Schema

### HTML Scraping Mode

Generated Markdown includes YAML front matter:

```yaml
---
title: "Article Title"
slug: "article-slug"
date: "2025-01-01"
canonical_url: "https://example.com/blog/2025/01/01/article-slug/"
description: "Meta description from WordPress"
category: "Category Name"
series_order: 1
wordpress_synced: "2025-01-01"
---
```

### REST API Mode (--api)

When fetching via REST API, you get enriched front matter with full metadata:

```yaml
---
title: "Article Title"
slug: "article-slug"
date: "2025-01-01"
modified: "2025-01-15"
description: "Article excerpt"
canonical_url: "https://example.com/article-slug/"
categories:
  - "Programming"
  - "JavaScript"
tags:
  - "closures"
  - "functions"
author: "Rajiv Pant"
featured_image: "https://example.com/uploads/image.jpg"
featured_image_alt: "Image description"
wordpress:
  post_id: 1234
  category_ids: [5, 12]
  tag_ids: [23, 45]
  author_id: 1
  synced_at: "2025-12-05T21:30:00Z"
---
```

A JSON sidecar file (`article-slug.json`) is also created with the complete API response for future bi-directional sync.

## Verification Checks

The verify command performs comprehensive QA:

- **Front matter validation** - Required fields present, valid formats
- **Markdown structure** - No unclosed links, code blocks, or HTML remnants
- **Word count comparison** - Detects significant content loss (>15% triggers error)
- **Heading preservation** - All section headings converted
- **URL/link preservation** - All article links present
- **Image preservation** - Images not lost in conversion
- **Code block preservation** - Code blocks converted correctly
- **List item preservation** - List items not lost
- **Sentence spot-checking** - Samples sentences for accuracy

**Exit codes:**
- `0` - All checks passed
- `1` - Issues found (errors)
- `2` - Warnings only

## WordPress Publishing

ownwords can publish directly to WordPress using the REST API with Application Passwords.

### Configure WordPress Connection

```bash
# Add a WordPress site
ownwords config-wp add myblog https://myblog.example.com --username=author
# You'll be prompted for the Application Password

# Add a site and set as default
ownwords config-wp add myblog https://myblog.example.com --username=author --default

# List configured sites
ownwords config-wp list

# Test connection
ownwords config-wp test myblog
```

### Creating an Application Password

You need a WordPress Application Password (not your regular login password) to use the REST API.

**For self-hosted WordPress:**

1. Go to your WordPress admin: `https://yoursite.com/wp-admin/`
2. Navigate to Users → Your Profile
3. Scroll to "Application Passwords" section
4. Enter a name (e.g., "ownwords") and click "Add New"
5. Copy the generated password (format: `xxxx xxxx xxxx xxxx xxxx xxxx`)

**For WordPress.com hosted sites:**

1. Go to <https://wordpress.com/me/security/two-step>
2. Scroll to "Application Passwords"
3. Enter a name (e.g., "ownwords") and click "Generate Password"
4. Copy the generated password
5. Use your **WordPress.com username** (not your site username) when configuring

**Security notes:**

- Credentials are stored in `~/.config/ownwords/config.json` with `600` permissions (owner read/write only)
- Application passwords can be revoked anytime without affecting your main password
- Never commit credentials to version control

### Publish to WordPress

```bash
# Publish as draft (default)
ownwords publish ./content/articles/my-article.md

# Publish immediately
ownwords publish ./content/articles/my-article.md --status=publish

# Update existing post (finds by slug)
ownwords publish ./content/articles/my-article.md --update

# Publish to specific site
ownwords publish ./content/articles/my-article.md --site=myblog

# Publish with a specific date (backdating)
ownwords publish ./content/articles/my-article.md --date="2025-12-07T23:00:00"

# Dry run (preview without publishing)
ownwords publish ./content/articles/my-article.md --dryrun
```

### Image Upload

When your markdown includes local images, ownwords automatically uploads them to WordPress:

```markdown
![My photo](./my-article/photo.jpg)
```

**Image handling:**

- Local images are automatically uploaded to WordPress media library
- Image URLs are rewritten in the published post
- Alt text from markdown is preserved
- An image tracking sidecar file (`my-article.images.json`) prevents re-uploading unchanged images
- After publishing, WordPress metadata (post_id, category_ids, etc.) is saved back to the markdown front matter

### Batch Publish

```bash
# Publish all markdown files in a directory
ownwords publish-all ./content/articles/

# Update all existing posts (redundant --status=publish since that's the default)
ownwords publish-all ./content/articles/ --update
```

### Publishing Safeguards

ownwords includes several safeguards to prevent common mistakes:

**1. Auto-detect existing posts**

If your markdown file has a `wordpress.post_id` in its front matter (saved from a previous publish), ownwords automatically enables `--update` mode to prevent creating duplicate drafts:

```
⚠️  SAFEGUARD: Found existing WordPress post_id: 6855
   Automatically enabling --update mode to prevent duplicate draft creation.
```

**2. Confirmation before creating new posts**

When creating a new post (not updating), ownwords prompts for confirmation:

```
⚠️  CREATING NEW POST
   This will create a new published post in WordPress.
   If you intended to update an existing post, cancel and use --update.

Continue? (y/N):
```

Use `--yes` to skip this prompt for automation.

**3. Enhanced dry-run**

The `--dryrun` flag shows a detailed preview including:
- Post details (title, slug, word count)
- Action that would be taken (UPDATE vs CREATE)
- Image upload plan (which images would be uploaded, which are already cached)
- Existing sidecar URLs that will be reused

```bash
# Always preview before publishing
ownwords publish ./content/my-article.md --dryrun
```

**4. Image upload tracking**

A sidecar file (`my-article.images.json`) tracks:
- Which images have been uploaded
- Their WordPress media IDs and URLs
- File hashes to detect changes

This prevents re-uploading unchanged images and ensures image URLs remain stable.

**Best practice workflow:**

```bash
# 1. Preview first
ownwords publish ./content/my-article.md --dryrun

# 2. Then publish
ownwords publish ./content/my-article.md
```

### Environment Variables

For CI/CD environments, use environment variables instead of the config file:

```bash
export OWNWORDS_WP_SITE=https://myblog.example.com
export OWNWORDS_WP_USERNAME=author
export OWNWORDS_WP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

### WordPress API in Code

```javascript
const { WpClient } = require('ownwords');

const client = new WpClient({
  url: 'https://myblog.example.com',
  username: 'author',
  appPassword: 'xxxx xxxx xxxx xxxx'
});

// Test connection
const result = await client.testConnection();
console.log(result.success ? 'Connected!' : result.error);

// Publish markdown file (with automatic image upload)
const post = await client.publishMarkdown('./content/my-article.md', {
  status: 'publish',
  update: true,  // Update if exists
  date: '2025-12-07T23:00:00'  // Optional: backdate the post
});
console.log(`Published: ${post.link}`);
console.log(`Images uploaded: ${post.imagesUploaded}`);

// Create post directly
const newPost = await client.createPost({
  title: 'My Article',
  content: '<p>Hello world</p>',
  status: 'draft'
});
```

## Dual Publishing Workflow

### Automated Workflow (Recommended)

1. **Edit locally**: Modify markdown files with your preferred editor or AI assistant
2. **Deploy static site**: Push changes to GitHub → Cloudflare Pages auto-deploys
3. **Publish to WordPress**: `ownwords publish ./content/articles/my-article.md --update`

### Manual Workflow

1. **Edit locally**: Modify markdown files
2. **Build**: Generate HTML for your static site
3. **Export**: Generate WordPress-ready HTML with `ownwords export`
4. **Copy to WordPress**: Paste exported HTML into WordPress editor
5. **Deploy static site**: Push changes to your static site host

## Known Limitations

### YouTube embeds become clickable thumbnails

When fetching WordPress articles, YouTube iframe embeds are converted to clickable thumbnail images linking to YouTube. This is because Markdown doesn't support iframes.

**Impact:** When you publish back to WordPress, the video appears as a thumbnail image that links to YouTube, not an embedded player.

**Workaround:** After publishing, manually edit the post in WordPress and replace the image with a YouTube embed block.

### WordPress galleries lose grid layout

WordPress gallery blocks use CSS classes to create multi-column layouts. During HTML→Markdown conversion, these classes are stripped. Images display as a vertical stack instead of a grid.

**Workaround:**
- Edit the markdown to add custom gallery markers
- Add gallery CSS to your build template
- Manually recreate the gallery in WordPress after publishing

### Image captions may separate from images

WordPress figure/figcaption relationships can be lost during conversion, with captions appearing as separate italic text.

**Workaround:** Use markdown image title syntax:
```markdown
[![Alt text](./image.jpg "Caption text")](./image.jpg)
```

### Date format includes time component

The `fetch --api` command outputs dates in ISO 8601 format with time (e.g., `2025-02-27T22:36:03`). Build tools that expect date-only format (`YYYY-MM-DD`) need to strip the time portion.

## Dependencies

This toolkit uses only Node.js built-in modules:
- `fs`
- `path`
- `child_process` (for curl)

No npm packages required for core functionality.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please open an issue to discuss before submitting PRs.

## Author

Rajiv Pant — [rajiv.com](https://rajiv.com)

---

## Built with synthesis coding

This project was built using [**synthesis coding**](https://synthesiscoding.com/) — the hands-on craft of rigorous AI-assisted development. Synthesis coding is the practical application of [**synthesis engineering**](https://synthesiscoding.com/), the broader discipline encompassing methodology, organizational practices, and systematic quality standards.

Both guided development:
- Human architectural decisions drove the design
- AI assisted with implementation, testing, and documentation
- Each feature was iteratively refined through human review

The toolkit itself supports Synthesis Coding workflows by enabling human authors to maintain ownership of their content while using AI for batch editorial tasks.

*Learn more at [synthesiscoding.com](https://synthesiscoding.com/)*
