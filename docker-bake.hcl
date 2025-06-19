group "default" {
  targets = ["vllm", "sglang"]
}

target "vllm" {
  context = "."
  dockerfile = "Dockerfile"
  args = {
    ROLLOUT_ENGINE = "vllm"
    REPO = "248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu"
    BASE_TAG = "base"
  }
  tags = ["248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:verl_vllm"]
  platforms = ["linux/amd64"]
  cache-to = ["type=registry,ref=248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:cache,mode=max"]
  cache-from = ["type=registry,ref=248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:cache"]
  push = true
}

target "sglang" {
  inherits = ["vllm"]
  args = {
    ROLLOUT_ENGINE = "sglang"
  }
  tags = ["248189905876.dkr.ecr.ap-south-1.amazonaws.com/yuxuanlu:verl_sglang"]
}
