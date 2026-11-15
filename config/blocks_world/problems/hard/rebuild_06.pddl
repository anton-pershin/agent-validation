(define (problem rebuild-06)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear a) (clear b) (clear c) (clear d) (clear e) (hand-empty) (on d f) (on-table a) (on-table b) (on-table c) (on-table e) (on-table f))
  (:goal (and (on a d) (on b e) (on c b) (on e f) (on f a) (on-table d)))
)
