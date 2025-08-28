const fs = require('fs');
const path = require('path');
const glob = require('glob');

// Standalone Python packaging script (replaces Grunt task)
const srcPattern = path.resolve(__dirname, '../py/**/*.py');
const destFile = path.resolve(__dirname, '../js/extra-python-modules.js');

console.log('Packaging Python modules...');

const files = glob.sync(srcPattern);
let output = '';

for (const file of files) {
    const content = fs.readFileSync(file, 'utf8').replace(/\r\n?/g, '\n');
    const modulePath = file.replace(/^.*runtime\/client\/py\//, 'src/lib/');
    output += `Sk.builtinFiles.files['${modulePath}']=${JSON.stringify(content)};`;
}

fs.writeFileSync(destFile, output, 'utf8');
console.log(`✅ Packaged ${files.length} Python files → ${path.relative(process.cwd(), destFile)}`);