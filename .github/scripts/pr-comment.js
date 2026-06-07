async function findPrComment({ github, owner, repo, issueNumber, marker }) {
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: issueNumber,
    per_page: 100,
  });

  return comments.find(
    comment => comment.user.type === 'Bot' && comment.body.includes(marker)
  );
}

async function upsertPrComment({
  github,
  context,
  marker,
  body,
  owner = context.repo.owner,
  repo = context.repo.repo,
  issueNumber = context.issue.number,
}) {
  if (!issueNumber) {
    throw new Error('issueNumber is required to update a PR comment');
  }

  const existing = await findPrComment({
    github,
    owner,
    repo,
    issueNumber,
    marker,
  });

  if (existing) {
    await github.rest.issues.updateComment({
      owner,
      repo,
      comment_id: existing.id,
      body,
    });
    return existing.id;
  }

  const created = await github.rest.issues.createComment({
    owner,
    repo,
    issue_number: issueNumber,
    body,
  });
  return created.data.id;
}

async function deletePrComment({
  github,
  context,
  marker,
  owner = context.repo.owner,
  repo = context.repo.repo,
  issueNumber = context.issue.number,
}) {
  if (!issueNumber) {
    throw new Error('issueNumber is required to delete a PR comment');
  }

  const existing = await findPrComment({
    github,
    owner,
    repo,
    issueNumber,
    marker,
  });

  if (!existing) {
    return false;
  }

  await github.rest.issues.deleteComment({
    owner,
    repo,
    comment_id: existing.id,
  });
  return true;
}

module.exports = {
  deletePrComment,
  upsertPrComment,
};
