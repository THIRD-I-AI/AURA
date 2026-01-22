#!/usr/bin/env node
/**
 * Orphan File Finder for AURA Frontend
 * Scans src/ for .tsx, .ts, and .css files that are never imported
 * Excludes main.tsx, App.tsx, vite-env.d.ts, and index.html references
 */

const fs = require('fs');
const path = require('path');

// Entry points that should never be marked as orphans
const EXCLUDE_FROM_ORPHAN_CHECK = new Set([
  'main.tsx',
  'App.tsx',
  'vite-env.d.ts',
  'vite.config.ts',
  'index.html',
  'tsconfig.json',
  'tsconfig.node.json',
]);

// Directories to skip
const SKIP_DIRS = new Set([
  'node_modules',
  'dist',
  'build',
  '.git',
  '__pycache__',
  'coverage',
  '.vite',
]);

/**
 * Get all TypeScript/CSS files in directory tree
 */
function getAllFiles(dir, fileList = []) {
  const files = fs.readdirSync(dir);

  files.forEach(file => {
    const filePath = path.join(dir, file);
    const stat = fs.statSync(filePath);

    if (stat.isDirectory()) {
      if (!SKIP_DIRS.has(file)) {
        getAllFiles(filePath, fileList);
      }
    } else {
      if (/\.(tsx?|css)$/.test(file)) {
        fileList.push(filePath);
      }
    }
  });

  return fileList;
}

/**
 * Extract all import statements from a file
 */
function extractImports(filePath) {
  try {
    const content = fs.readFileSync(filePath, 'utf8');
    const imports = new Set();

    // Match various import patterns
    const patterns = [
      // import ... from './path'
      /import\s+(?:{[^}]+}|[\w*]+)\s+from\s+['"]([^'"]+)['"]/g,
      // import('./path')
      /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
      // @import './path' (CSS)
      /@import\s+['"]([^'"]+)['"]/g,
      // require('./path')
      /require\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    ];

    patterns.forEach(pattern => {
      let match;
      while ((match = pattern.exec(content)) !== null) {
        imports.add(match[1]);
      }
    });

    return imports;
  } catch (error) {
    console.error(`Error reading ${filePath}:`, error.message);
    return new Set();
  }
}

/**
 * Normalize import path to actual file path
 */
function normalizeImportPath(importPath, fromDir, projectRoot) {
  // Remove query params and fragments
  importPath = importPath.split('?')[0].split('#')[0];

  // Skip external packages
  if (!importPath.startsWith('.') && !importPath.startsWith('/')) {
    return null;
  }

  // Resolve relative path
  const basePath = importPath.startsWith('/') 
    ? path.join(projectRoot, importPath)
    : path.resolve(fromDir, importPath);

  // Try different extensions
  const extensions = ['', '.ts', '.tsx', '.js', '.jsx', '.css'];
  
  for (const ext of extensions) {
    const fullPath = basePath + ext;
    if (fs.existsSync(fullPath)) {
      return fullPath;
    }
  }

  // Try index files
  for (const ext of ['.ts', '.tsx', '.js', '.jsx']) {
    const indexPath = path.join(basePath, `index${ext}`);
    if (fs.existsSync(indexPath)) {
      return indexPath;
    }
  }

  return null;
}

/**
 * Find orphan files
 */
function findOrphanFiles(srcDir) {
  console.log(`🔍 Scanning ${srcDir} for orphan files...\n`);

  const allFiles = getAllFiles(srcDir);
  const importedFiles = new Set();
  const projectRoot = path.dirname(srcDir);

  console.log(`Found ${allFiles.length} TypeScript/CSS files\n`);

  // Build import graph
  allFiles.forEach(file => {
    const imports = extractImports(file);
    const fileDir = path.dirname(file);

    imports.forEach(importPath => {
      const resolvedPath = normalizeImportPath(importPath, fileDir, projectRoot);
      if (resolvedPath && fs.existsSync(resolvedPath)) {
        importedFiles.add(path.normalize(resolvedPath));
      }
    });
  });

  // Check index.html for script references
  const indexHtml = path.join(projectRoot, 'index.html');
  if (fs.existsSync(indexHtml)) {
    const htmlContent = fs.readFileSync(indexHtml, 'utf8');
    const scriptMatches = htmlContent.matchAll(/src=["']([^"']+)["']/g);
    
    for (const match of scriptMatches) {
      const scriptPath = path.join(projectRoot, match[1]);
      if (fs.existsSync(scriptPath)) {
        importedFiles.add(path.normalize(scriptPath));
      }
    }
  }

  // Find orphans
  const orphans = [];
  const excluded = [];

  allFiles.forEach(file => {
    const normalizedFile = path.normalize(file);
    const fileName = path.basename(file);

    if (EXCLUDE_FROM_ORPHAN_CHECK.has(fileName)) {
      excluded.push(file);
    } else if (!importedFiles.has(normalizedFile)) {
      orphans.push(file);
    }
  });

  return {
    total: allFiles.length,
    orphans,
    excluded,
    imported: allFiles.length - orphans.length - excluded.length,
  };
}

/**
 * Main entry point
 */
function main() {
  console.log('='.repeat(80));
  console.log('AURA FRONTEND ORPHAN FILE FINDER');
  console.log('='.repeat(80));
  console.log();

  const projectRoot = __dirname;
  const srcDir = path.join(projectRoot, 'frontend', 'src');

  if (!fs.existsSync(srcDir)) {
    console.error(`❌ Error: ${srcDir} does not exist!`);
    process.exit(1);
  }

  const results = findOrphanFiles(srcDir);

  console.log('='.repeat(80));
  console.log('RESULTS');
  console.log('='.repeat(80));
  console.log();

  console.log(`📊 Total files scanned: ${results.total}`);
  console.log(`✓  Excluded (entry points): ${results.excluded.length}`);
  console.log(`✓  Imported files: ${results.imported}`);
  console.log(`🗑️  Potential orphans: ${results.orphans.length}`);
  console.log();

  if (results.orphans.length > 0) {
    console.log('⚠️  ORPHAN FILES DETECTED:');
    console.log('='.repeat(80));

    // Group by directory
    const byDir = {};
    results.orphans.forEach(file => {
      const dir = path.dirname(file);
      if (!byDir[dir]) byDir[dir] = [];
      byDir[dir].push(path.basename(file));
    });

    Object.keys(byDir).sort().forEach(dir => {
      const relDir = path.relative(projectRoot, dir);
      console.log(`\n📁 ${relDir}/`);
      byDir[dir].sort().forEach(file => {
        console.log(`   - ${file}`);
      });
    });

    console.log('\n' + '='.repeat(80));
    console.log('RECOMMENDATIONS');
    console.log('='.repeat(80));
    console.log();
    console.log('1. Review each orphan file manually');
    console.log('2. Check if it\'s genuinely unused or referenced dynamically');
    console.log('3. If confirmed unused, consider deleting or moving to /backups');
    console.log('4. Some files may be assets or utilities - verify before deletion');
    console.log();
    console.log('⚠️  IMPORTANT: Always backup before deleting files!');
  } else {
    console.log('✅ No orphan files found! All files are imported somewhere.');
  }

  console.log();
}

main();
