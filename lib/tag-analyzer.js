/**
 * @fileoverview AI-powered tag analysis for content taxonomy
 * @module ownwords/tag-analyzer
 *
 * Implements a three-phase approach to intelligent tag assignment:
 * 1. Article Analysis - Analyze each article and suggest tags (bottom-up)
 * 2. Taxonomy Synthesis - Create unified taxonomy from all suggestions
 * 3. Tag Assignment - Assign tags from approved taxonomy to articles
 *
 * This approach ensures tags emerge from actual content rather than
 * being imposed by keyword matching rules.
 */

const fs = require('fs');
const path = require('path');
const { ClaudeClient } = require('./claude-api');

/**
 * Special tags that should be preserved and not replaced during analysis
 * These serve display/modifier purposes beyond content description
 */
const PRESERVED_TAGS = ['featured'];

/**
 * Tag Analyzer for AI-powered content taxonomy
 */
class TagAnalyzer {
  /**
   * Create a TagAnalyzer instance
   *
   * @param {Object} options - Configuration options
   * @param {string[]} options.contentDirs - Array of content directories to analyze
   * @param {string} options.outputDir - Directory to store analysis results
   * @param {Object} [options.claudeOptions] - Options to pass to ClaudeClient
   * @param {boolean} [options.verbose=false] - Enable verbose output
   *
   * @example
   * const analyzer = new TagAnalyzer({
   *   contentDirs: [
   *     './rajiv-site/content/posts',
   *     './synthesis-coding-site/content/posts',
   *     './synthesis-engineering-site/content/posts'
   *   ],
   *   outputDir: './tag-analysis'
   * });
   */
  constructor(options) {
    this.contentDirs = options.contentDirs || [];
    this.outputDir = options.outputDir || './tag-analysis';
    this.verbose = options.verbose || false;
    this.claudeOptions = options.claudeOptions || {};

    // Lazily initialize Claude client
    this._claudeClient = null;
  }

  /**
   * Get or create Claude client
   * @private
   */
  _getClient() {
    if (!this._claudeClient) {
      this._claudeClient = new ClaudeClient(this.claudeOptions);
    }
    return this._claudeClient;
  }

  /**
   * Ensure output directory exists
   * @private
   */
  _ensureOutputDir() {
    if (!fs.existsSync(this.outputDir)) {
      fs.mkdirSync(this.outputDir, { recursive: true });
    }
  }

  /**
   * Find all article directories across all content dirs
   * Supports hierarchical structure: posts/YYYY/MM/slug/index.md
   *
   * @returns {Object[]} Array of article info
   */
  findAllArticles() {
    const articles = [];

    for (const contentDir of this.contentDirs) {
      const postsDir = contentDir;

      if (!fs.existsSync(postsDir)) {
        if (this.verbose) {
          console.log(`  Skipping (not found): ${postsDir}`);
        }
        continue;
      }

      // Walk the directory structure
      const walkDir = (dir, depth = 0) => {
        const entries = fs.readdirSync(dir, { withFileTypes: true });

        for (const entry of entries) {
          const fullPath = path.join(dir, entry.name);

          if (entry.isDirectory()) {
            // Check if this directory has an index.md
            const indexPath = path.join(fullPath, 'index.md');
            if (fs.existsSync(indexPath)) {
              articles.push({
                slug: entry.name,
                path: fullPath,
                indexPath,
                sourceRepo: this._getRepoName(contentDir)
              });
            } else {
              // Recurse into subdirectories (for YYYY/MM structure)
              walkDir(fullPath, depth + 1);
            }
          }
        }
      };

      walkDir(postsDir);
    }

    return articles;
  }

  /**
   * Extract repo name from content directory path
   * @private
   */
  _getRepoName(contentDir) {
    const parts = contentDir.split(path.sep);
    // Find the repo name (usually the directory before 'content')
    const contentIndex = parts.indexOf('content');
    if (contentIndex > 0) {
      return parts[contentIndex - 1];
    }
    return parts[parts.length - 1];
  }

  /**
   * Parse front matter from markdown content
   * @private
   */
  _parseFrontMatter(content) {
    const match = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
    if (!match) {
      return { frontMatter: {}, body: content };
    }

    const fmString = match[1];
    const body = match[2];
    const frontMatter = {};

    // Simple YAML parsing
    let currentList = null;

    for (const line of fmString.split('\n')) {
      // Handle list items
      if (line.match(/^\s+-\s+/)) {
        if (currentList !== null) {
          const item = line.replace(/^\s+-\s+/, '').trim().replace(/^["']|["']$/g, '');
          currentList.push(item);
        }
        continue;
      }

      // Handle key: value pairs
      const colonIndex = line.indexOf(':');
      if (colonIndex > 0 && !line.startsWith(' ')) {
        const key = line.substring(0, colonIndex).trim();
        const value = line.substring(colonIndex + 1).trim();

        if (value === '' || value === '[]') {
          // Start of a list
          frontMatter[key] = [];
          currentList = frontMatter[key];
        } else {
          // Regular value
          frontMatter[key] = value.replace(/^["']|["']$/g, '');
          currentList = null;
        }
      }
    }

    return { frontMatter, body };
  }

  /**
   * Load an article's content
   * @private
   */
  _loadArticle(articleInfo) {
    const content = fs.readFileSync(articleInfo.indexPath, 'utf-8');
    const { frontMatter, body } = this._parseFrontMatter(content);

    return {
      ...articleInfo,
      title: frontMatter.title || articleInfo.slug,
      description: frontMatter.description || '',
      date: frontMatter.date || '',
      existingTags: frontMatter.tags || [],
      existingCategories: frontMatter.categories || [],
      body,
      wordCount: body.split(/\s+/).length
    };
  }

  /**
   * Phase 1: Analyze all articles and suggest tags
   *
   * @param {Object} [options] - Analysis options
   * @param {number} [options.batchSize=20] - Articles per batch for progress reporting
   * @param {number} [options.delayMs=500] - Delay between API calls (rate limiting)
   * @param {boolean} [options.resume=true] - Resume from previous run if exists
   * @returns {Promise<Object>} Analysis results
   *
   * @example
   * const results = await analyzer.analyzeAllArticles({ batchSize: 20 });
   */
  async analyzeAllArticles(options = {}) {
    const {
      batchSize = 20,
      delayMs = 500,
      resume = true
    } = options;

    this._ensureOutputDir();

    const outputFile = path.join(this.outputDir, 'phase1-suggestions.json');
    const progressFile = path.join(this.outputDir, 'phase1-progress.json');

    // Load existing progress if resuming
    let processed = {};
    let startIndex = 0;

    if (resume && fs.existsSync(progressFile)) {
      const progress = JSON.parse(fs.readFileSync(progressFile, 'utf-8'));
      processed = progress.processed || {};
      startIndex = progress.lastIndex || 0;
      console.log(`Resuming from article ${startIndex}...`);
    }

    const articles = this.findAllArticles();
    console.log(`Found ${articles.length} articles across ${this.contentDirs.length} repos`);

    const client = this._getClient();
    const results = [];
    let errorCount = 0;

    for (let i = startIndex; i < articles.length; i++) {
      const articleInfo = articles[i];

      // Skip if already processed
      if (processed[articleInfo.slug]) {
        results.push(processed[articleInfo.slug]);
        continue;
      }

      try {
        const article = this._loadArticle(articleInfo);

        console.log(`[${i + 1}/${articles.length}] Analyzing: ${article.title.substring(0, 50)}...`);

        const suggestion = await client.suggestTags({
          title: article.title,
          body: article.body,
          description: article.description,
          existingTags: article.existingTags
        });

        const result = {
          slug: article.slug,
          title: article.title,
          sourceRepo: article.sourceRepo,
          path: article.path,
          date: article.date,
          existingTags: article.existingTags,
          existingCategories: article.existingCategories,
          ...suggestion,
          articleTitle: article.title // For taxonomy synthesis
        };

        results.push(result);
        processed[article.slug] = result;

        // Save progress after each article
        fs.writeFileSync(progressFile, JSON.stringify({
          lastIndex: i + 1,
          processed
        }, null, 2));

        if (this.verbose) {
          console.log(`   Tags: ${suggestion.suggestedTags?.join(', ') || 'none'}`);
        }

        // Rate limiting
        if (delayMs > 0) {
          await new Promise(resolve => setTimeout(resolve, delayMs));
        }

      } catch (error) {
        console.error(`   Error: ${error.message}`);
        errorCount++;

        results.push({
          slug: articleInfo.slug,
          sourceRepo: articleInfo.sourceRepo,
          path: articleInfo.path,
          error: error.message
        });
      }

      // Batch progress
      if ((i + 1) % batchSize === 0) {
        console.log(`\n--- Progress: ${i + 1}/${articles.length} (${Math.round((i + 1) / articles.length * 100)}%) ---\n`);
      }
    }

    // Save final results
    const output = {
      generatedAt: new Date().toISOString(),
      totalArticles: articles.length,
      successCount: results.filter(r => !r.error).length,
      errorCount,
      contentDirs: this.contentDirs,
      suggestions: results
    };

    fs.writeFileSync(outputFile, JSON.stringify(output, null, 2));
    console.log(`\nPhase 1 complete: ${outputFile}`);

    return output;
  }

  /**
   * Phase 2: Synthesize unified taxonomy from suggestions
   *
   * @param {Object} [options] - Synthesis options
   * @param {string} [options.suggestionsFile] - Path to phase 1 output (default: auto)
   * @param {number} [options.maxTags=100] - Maximum tags in final taxonomy
   * @returns {Promise<Object>} Taxonomy results
   *
   * @example
   * const taxonomy = await analyzer.synthesizeTaxonomy({ maxTags: 80 });
   */
  async synthesizeTaxonomy(options = {}) {
    const suggestionsFile = options.suggestionsFile ||
      path.join(this.outputDir, 'phase1-suggestions.json');
    const maxTags = options.maxTags || 100;

    if (!fs.existsSync(suggestionsFile)) {
      throw new Error(`Phase 1 results not found: ${suggestionsFile}`);
    }

    console.log('Loading Phase 1 suggestions...');
    const phase1 = JSON.parse(fs.readFileSync(suggestionsFile, 'utf-8'));

    const validSuggestions = phase1.suggestions.filter(s => !s.error && s.suggestedTags);
    console.log(`Found ${validSuggestions.length} articles with tag suggestions`);

    const client = this._getClient();

    console.log('Synthesizing unified taxonomy...');
    const taxonomy = await client.synthesizeTaxonomy(validSuggestions, { maxTags });

    // Add preserved tags to taxonomy if not present
    for (const preservedTag of PRESERVED_TAGS) {
      const exists = taxonomy.taxonomy?.some(t =>
        t.tag.toLowerCase() === preservedTag.toLowerCase()
      );
      if (!exists && taxonomy.taxonomy) {
        taxonomy.taxonomy.push({
          tag: preservedTag,
          aliases: [],
          description: 'Special modifier tag (preserved)',
          parent: null,
          preserved: true
        });
      }
    }

    // Save taxonomy
    const outputFile = path.join(this.outputDir, 'phase2-taxonomy.json');
    const output = {
      generatedAt: new Date().toISOString(),
      basedOnArticles: phase1.totalArticles,
      ...taxonomy
    };

    fs.writeFileSync(outputFile, JSON.stringify(output, null, 2));
    console.log(`\nPhase 2 complete: ${outputFile}`);

    // Also save human-readable version
    const readableFile = path.join(this.outputDir, 'taxonomy.txt');
    const readable = this._formatTaxonomyReadable(taxonomy);
    fs.writeFileSync(readableFile, readable);
    console.log(`Readable taxonomy: ${readableFile}`);

    return output;
  }

  /**
   * Format taxonomy for human review
   * @private
   */
  _formatTaxonomyReadable(taxonomy) {
    const lines = [
      '# Tag Taxonomy',
      `Generated: ${new Date().toISOString()}`,
      '',
      `Total tags: ${taxonomy.taxonomy?.length || 0}`,
      '',
      '## Tags',
      ''
    ];

    if (taxonomy.taxonomy) {
      // Group by parent
      const byParent = {};
      const roots = [];

      for (const tag of taxonomy.taxonomy) {
        if (tag.parent) {
          if (!byParent[tag.parent]) byParent[tag.parent] = [];
          byParent[tag.parent].push(tag);
        } else {
          roots.push(tag);
        }
      }

      // Print roots first, then children
      for (const tag of roots) {
        lines.push(`- ${tag.tag}${tag.preserved ? ' [PRESERVED]' : ''}`);
        if (tag.description) lines.push(`  ${tag.description}`);
        if (tag.aliases?.length > 0) lines.push(`  Aliases: ${tag.aliases.join(', ')}`);

        // Print children
        if (byParent[tag.tag]) {
          for (const child of byParent[tag.tag]) {
            lines.push(`  - ${child.tag}`);
            if (child.description) lines.push(`    ${child.description}`);
          }
        }
        lines.push('');
      }
    }

    if (taxonomy.merges && Object.keys(taxonomy.merges).length > 0) {
      lines.push('## Merges', '');
      for (const [old, canonical] of Object.entries(taxonomy.merges)) {
        lines.push(`- "${old}" → "${canonical}"`);
      }
      lines.push('');
    }

    if (taxonomy.removed?.length > 0) {
      lines.push('## Removed Tags', '');
      for (const removed of taxonomy.removed) {
        lines.push(`- ${removed}`);
      }
    }

    return lines.join('\n');
  }

  /**
   * Phase 3: Assign tags from taxonomy to all articles
   *
   * @param {Object} [options] - Assignment options
   * @param {string} [options.taxonomyFile] - Path to phase 2 output (default: auto)
   * @param {boolean} [options.dryRun=false] - If true, don't modify files
   * @param {number} [options.delayMs=300] - Delay between API calls
   * @returns {Promise<Object>} Assignment results
   *
   * @example
   * const results = await analyzer.assignTags({ dryRun: true });
   */
  async assignTags(options = {}) {
    const {
      taxonomyFile = path.join(this.outputDir, 'phase2-taxonomy.json'),
      dryRun = false,
      delayMs = 300,
      resume = true
    } = options;

    if (!fs.existsSync(taxonomyFile)) {
      throw new Error(`Phase 2 taxonomy not found: ${taxonomyFile}`);
    }

    console.log('Loading taxonomy...');
    const taxonomy = JSON.parse(fs.readFileSync(taxonomyFile, 'utf-8'));

    const outputFile = path.join(this.outputDir, 'phase3-assignments.json');
    const progressFile = path.join(this.outputDir, 'phase3-progress.json');

    // Load existing progress if resuming
    let processed = {};
    let startIndex = 0;

    if (resume && fs.existsSync(progressFile)) {
      const progress = JSON.parse(fs.readFileSync(progressFile, 'utf-8'));
      processed = progress.processed || {};
      startIndex = progress.lastIndex || 0;
      console.log(`Resuming from article ${startIndex}...`);
    }

    const articles = this.findAllArticles();
    console.log(`Processing ${articles.length} articles...`);

    const client = this._getClient();
    const results = [];
    let modifiedCount = 0;

    for (let i = startIndex; i < articles.length; i++) {
      const articleInfo = articles[i];

      // Skip if already processed
      if (processed[articleInfo.slug]) {
        results.push(processed[articleInfo.slug]);
        if (processed[articleInfo.slug].modified) modifiedCount++;
        continue;
      }

      try {
        const article = this._loadArticle(articleInfo);

        console.log(`[${i + 1}/${articles.length}] ${article.title.substring(0, 50)}...`);

        const assignment = await client.assignTagsFromTaxonomy(
          {
            title: article.title,
            body: article.body,
            description: article.description
          },
          taxonomy
        );

        // Preserve special tags from original
        const preservedFromOriginal = article.existingTags.filter(t =>
          PRESERVED_TAGS.includes(t.toLowerCase())
        );

        const finalTags = [
          ...(assignment.assignedTags || []),
          ...preservedFromOriginal
        ].filter((tag, index, arr) =>
          arr.findIndex(t => t.toLowerCase() === tag.toLowerCase()) === index
        );

        const result = {
          slug: article.slug,
          title: article.title,
          sourceRepo: article.sourceRepo,
          path: article.path,
          indexPath: article.indexPath,
          existingTags: article.existingTags,
          assignedTags: finalTags,
          reasoning: assignment.reasoning,
          preservedTags: preservedFromOriginal,
          modified: JSON.stringify(article.existingTags.sort()) !== JSON.stringify(finalTags.sort())
        };

        results.push(result);
        processed[article.slug] = result;

        if (result.modified) {
          modifiedCount++;
          if (this.verbose) {
            console.log(`   Old: ${article.existingTags.join(', ')}`);
            console.log(`   New: ${finalTags.join(', ')}`);
          }
        }

        // Save progress
        fs.writeFileSync(progressFile, JSON.stringify({
          lastIndex: i + 1,
          processed
        }, null, 2));

        if (delayMs > 0) {
          await new Promise(resolve => setTimeout(resolve, delayMs));
        }

      } catch (error) {
        console.error(`   Error: ${error.message}`);
        results.push({
          slug: articleInfo.slug,
          path: articleInfo.path,
          error: error.message
        });
      }
    }

    // Save results
    const output = {
      generatedAt: new Date().toISOString(),
      dryRun,
      totalArticles: articles.length,
      modifiedCount,
      unchangedCount: articles.length - modifiedCount,
      preservedTags: PRESERVED_TAGS,
      assignments: results
    };

    fs.writeFileSync(outputFile, JSON.stringify(output, null, 2));
    console.log(`\nPhase 3 complete: ${outputFile}`);
    console.log(`  Modified: ${modifiedCount}`);
    console.log(`  Unchanged: ${articles.length - modifiedCount}`);

    return output;
  }

  /**
   * Apply tag assignments to local markdown files
   *
   * @param {Object} [options] - Apply options
   * @param {string} [options.assignmentsFile] - Path to phase 3 output (default: auto)
   * @param {boolean} [options.dryRun=false] - If true, don't modify files
   * @returns {Object} Apply results
   */
  applyToFiles(options = {}) {
    const {
      assignmentsFile = path.join(this.outputDir, 'phase3-assignments.json'),
      dryRun = false
    } = options;

    if (!fs.existsSync(assignmentsFile)) {
      throw new Error(`Assignments file not found: ${assignmentsFile}`);
    }

    console.log('Loading assignments...');
    const rawData = JSON.parse(fs.readFileSync(assignmentsFile, 'utf-8'));

    // Detect format: phase3-assignments has { assignments: [...] }
    // Simple format is just an array with { path, finalTags, category }
    const isSimpleFormat = Array.isArray(rawData);
    const assignments = isSimpleFormat ? rawData : rawData.assignments;

    if (!assignments || !Array.isArray(assignments)) {
      throw new Error('Invalid assignments file format');
    }

    console.log(`Format: ${isSimpleFormat ? 'simple (finalTags)' : 'phase3 (assignedTags)'}`);
    console.log(`Total entries: ${assignments.length}`);

    const results = {
      total: assignments.length,
      modified: 0,
      unchanged: 0,
      errors: 0,
      notFound: 0,
      changes: []
    };

    for (const assignment of assignments) {
      // Handle both formats
      const filePath = assignment.path || assignment.indexPath;
      const newTags = assignment.finalTags || assignment.assignedTags || [];
      const newCategory = assignment.category; // Only in simple format

      // Skip if using phase3 format and marked as not modified
      if (!isSimpleFormat && (assignment.error || !assignment.modified)) {
        if (!assignment.error) results.unchanged++;
        continue;
      }

      if (!filePath) {
        console.error('Skipping entry with no path');
        results.errors++;
        continue;
      }

      if (!fs.existsSync(filePath)) {
        if (this.verbose) {
          console.log(`NOT FOUND: ${filePath}`);
        }
        results.notFound++;
        continue;
      }

      try {
        const content = fs.readFileSync(filePath, 'utf-8');

        // Check if content has front matter
        if (!content.startsWith('---')) {
          console.error(`NO FRONT MATTER: ${filePath}`);
          results.errors++;
          continue;
        }

        const newContent = this._updateFrontMatter(content, {
          tags: newTags,
          category: newCategory
        });

        // Check if anything changed
        if (newContent === content) {
          results.unchanged++;
          if (this.verbose) {
            console.log(`UNCHANGED: ${filePath}`);
          }
          continue;
        }

        if (!dryRun) {
          fs.writeFileSync(filePath, newContent);
        }

        results.modified++;
        results.changes.push({
          path: filePath,
          slug: assignment.slug || path.basename(path.dirname(filePath)),
          category: newCategory,
          newTags
        });

        if (this.verbose || dryRun) {
          const slug = assignment.slug || path.basename(path.dirname(filePath));
          console.log(`${dryRun ? '[DRY RUN] ' : ''}UPDATED: ${slug}`);
          if (newCategory) console.log(`  Category: ${newCategory}`);
          if (newTags.length > 0) console.log(`  Tags: ${newTags.join(', ')}`);
        }

      } catch (error) {
        console.error(`Error updating ${filePath}: ${error.message}`);
        results.errors++;
      }
    }

    console.log(`\n${dryRun ? '[DRY RUN] ' : ''}Applied to files:`);
    console.log(`  Total: ${results.total}`);
    console.log(`  Modified: ${results.modified}`);
    console.log(`  Unchanged: ${results.unchanged}`);
    console.log(`  Not found: ${results.notFound}`);
    console.log(`  Errors: ${results.errors}`);

    return results;
  }

  /**
   * Update tags in markdown content
   * @private
   */
  _updateTagsInContent(content, newTags) {
    return this._updateFrontMatter(content, { tags: newTags });
  }

  /**
   * Update front matter with new tags and/or category
   * Preserves special tags like 'featured'
   * @private
   * @param {string} content - Full markdown content
   * @param {Object} updates - { tags?: string[], category?: string }
   * @returns {string} Updated content
   */
  _updateFrontMatter(content, updates) {
    // Find the front matter section
    const fmMatch = content.match(/^(---\n)([\s\S]*?)(\n---)/);
    if (!fmMatch) return content;

    const before = fmMatch[1];
    let frontMatter = fmMatch[2];
    const after = fmMatch[3];
    const body = content.substring(fmMatch[0].length);

    // Handle tags update
    if (updates.tags !== undefined) {
      // Check if 'featured' tag exists in current front matter
      const hasFeatured = /^\s*-\s*['"]?featured['"]?\s*$/m.test(frontMatter);
      let newTags = updates.tags;

      // Preserve featured tag if it was present
      if (hasFeatured && !newTags.includes('featured')) {
        newTags = [...newTags, 'featured'];
      }

      // Remove existing tags block (list format). Items may end at
      // end-of-string when the block is last before the closing ---.
      frontMatter = frontMatter.replace(/tags:\s*\n(?:\s+-\s+.*(?:\n|$))*/g, '');
      // Remove existing tags block (inline format)
      frontMatter = frontMatter.replace(/tags:\s*\[.*\]\s*\n?/g, '');

      // Build new tags block
      const tagsBlock = newTags.length > 0
        ? 'tags:\n' + newTags.sort().map(t => `  - "${t}"`).join('\n') + '\n'
        : 'tags: []\n';

      // Add tags before closing --- (at end of front matter)
      frontMatter = frontMatter.trimEnd() + '\n' + tagsBlock;
    }

    // Handle category update
    if (updates.category !== undefined && updates.category !== '') {
      const categoryYaml = `categories:\n  - "${updates.category}"`;

      // Remove existing categories block (list format)
      if (/^categories:\s*\n(?:\s+-\s+.*(?:\n|$))*/m.test(frontMatter)) {
        frontMatter = frontMatter.replace(/categories:\s*\n(?:\s+-\s+.*(?:\n|$))*/g, categoryYaml + '\n');
      }
      // Remove existing categories block (inline format)
      else if (/^categories:\s*\[.*\]\s*$/m.test(frontMatter)) {
        frontMatter = frontMatter.replace(/categories:\s*\[.*\]\s*\n?/gm, categoryYaml + '\n');
      }
      // Add categories before tags or at end
      else if (/^tags:/m.test(frontMatter)) {
        frontMatter = frontMatter.replace(/^(tags:)/m, categoryYaml + '\n$1');
      } else {
        frontMatter = frontMatter.trimEnd() + '\n' + categoryYaml + '\n';
      }
    }

    return before + frontMatter + after + body;
  }

  /**
   * Generate a summary report
   *
   * @returns {string} Summary report
   */
  generateReport() {
    const lines = ['# Tag Analysis Report', ''];

    const phase1File = path.join(this.outputDir, 'phase1-suggestions.json');
    const phase2File = path.join(this.outputDir, 'phase2-taxonomy.json');
    const phase3File = path.join(this.outputDir, 'phase3-assignments.json');

    if (fs.existsSync(phase1File)) {
      const p1 = JSON.parse(fs.readFileSync(phase1File, 'utf-8'));
      lines.push('## Phase 1: Article Analysis');
      lines.push(`- Total articles: ${p1.totalArticles}`);
      lines.push(`- Successfully analyzed: ${p1.successCount}`);
      lines.push(`- Errors: ${p1.errorCount}`);
      lines.push('');
    }

    if (fs.existsSync(phase2File)) {
      const p2 = JSON.parse(fs.readFileSync(phase2File, 'utf-8'));
      lines.push('## Phase 2: Taxonomy Synthesis');
      lines.push(`- Final tags: ${p2.taxonomy?.length || 0}`);
      lines.push(`- Merges: ${Object.keys(p2.merges || {}).length}`);
      lines.push(`- Removed: ${p2.removed?.length || 0}`);
      lines.push('');
    }

    if (fs.existsSync(phase3File)) {
      const p3 = JSON.parse(fs.readFileSync(phase3File, 'utf-8'));
      lines.push('## Phase 3: Tag Assignment');
      lines.push(`- Total articles: ${p3.totalArticles}`);
      lines.push(`- Modified: ${p3.modifiedCount}`);
      lines.push(`- Unchanged: ${p3.unchangedCount}`);
      lines.push('');
    }

    return lines.join('\n');
  }
}

module.exports = { TagAnalyzer, PRESERVED_TAGS };
